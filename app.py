import os
import datetime

from flask import Flask, flash, redirect, render_template, url_for, request, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

from helpers import login_required, lookup, usd

# Load environment variables from .env file
load_dotenv()

# Configure application
app = Flask(__name__)

# Set up secret key 
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')

# Configure session to use filesystem (instead of signed cookies)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  
Session(app)

# Ensure responses are not cached
@app.after_request
def after_request(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Expires'] = 0
    response.headers['Pragma'] = 'no-cache'
    return response

# Configure database with SQLAlchemy
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'finance.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Define users database model (table)
class Users(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    hash = db.Column(db.String(255), nullable=False)
    cash = db.Column(db.Float, nullable=False, server_default='10000')

    # Establish relationship with transactions
    transactions = relationship('Transactions', back_populates='user')

# Define transactions database model (table)
class Transactions(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    stock = db.Column(db.String(80), nullable=False)
    shares = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False)

    # Establish relationship with users
    user = relationship('Users', back_populates='transactions')

# Create database tables if thet don't exist
with app.app_context():
    db.create_all()

# Custom filter
app.jinja_env.filters['usd'] = usd

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():

    # Get user id
    user_id = session['user_id']

    # Query for stocks, price, shares and total value
    transactions_db = db.session.query(
        Transactions.stock,
        func.sum(Transactions.shares).label('shares'),
    ).group_by(Transactions.stock).filter_by(user_id = user_id).all()

    # Query user cash
    cash_db = db.session.query(Users.cash).filter_by(id = user_id).first()
    user_cash = round(cash_db.cash, 2)

    # Declare total value
    total_value = user_cash

    # Create a list to hold transaction values
    transactions_values = []

    # Declare value transactions
    for transaction in transactions_db:
        quote = lookup(transaction.stock)
        stock_price = quote['price']
        stock_value = round(stock_price * transaction.shares, 2)
        total_value += stock_value

        # Convert row object to dictionary and add additional fields
        transaction_dict = {
            'stock': transaction.stock,
            'shares': transaction.shares,
            'stock_price': stock_price,
            'stock_value': stock_value
        }
        
        # Append the modified transaction to the list
        transactions_values.append(transaction_dict)

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':
        return render_template('index.html', transactions=transactions_values,
                               user_cash=user_cash,
                               total_value=total_value)

    # User reached route via POST (as by submitting a form via POST)
    else:
        return redirect(url_for('index'))

@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy():

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':
        return render_template('buy.html')

    # User reached route via POST (as by submitting a form via POST)
    else:

        # Get user id
        user_id = session['user_id']

        # Ensure symbol was submitted
        symbol = request.form.get('symbol')
        if not symbol:
            flash('Must provide stock symbol', 'warning')
            return redirect(url_for('buy'))

        # Ensure symbol is valid
        stock = lookup(symbol)
        if stock is None:
            flash('Invalid stock symbol', 'warning')
            return redirect(url_for('buy'))

        # Ensure shares are a positive number
        shares = request.form.get('shares')
        if not shares or not shares.isdigit() or int(shares) <= 0:
            flash('Shares must be a positive integer number', 'warning')
            return redirect(url_for('buy'))

        # Declare total purchase value and users total cash
        transaction_value = int(shares) * stock['price']

        # Query user cash
        cash_db = db.session.query(Users.cash).filter_by(id = user_id).first()
        user_cash = cash_db.cash

        # Ensure user can afford the number of shares at the current price
        if user_cash < transaction_value:
            flash('Not enough funds', 'warning')
            return redirect(url_for('buy'))

        # Update user cash
        cash_updated = user_cash - transaction_value
        db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})
        
        # Declare a new transaction with user_id, stock, price, shares and date
        new_transaction = Transactions(
            user_id=user_id,
            stock = symbol,
            price=stock['price'],
            shares=int(shares),
            date=datetime.datetime.now()
        )
        
        # Add (insert) and commit the new transaction and cash into transactions table
        db.session.add(new_transaction)
        db.session.commit()

        # Display confirmation message
        flash('Transaction successful!', 'success')

        # Redirect user to homepage
        return redirect(url_for('index'))

@app.route('/history')
@login_required
def history():

    user_id = session['user_id']

    # Query transactions for the logged-in user
    transactions_db = db.session.query(
        Transactions.stock,
        Transactions.price,
        Transactions.shares,
        Transactions.date,
    ).filter_by(user_id=user_id).all()

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':
        return render_template('history.html', transactions=transactions_db)

    else:
        return redirect(url_for('index'))

@app.route('/login', methods=['GET', 'POST'])
def login():

    # Forget any user_id
    session.clear()

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':

        # Check for error messages in the URL
        error_message = request.args.get('error')

        return render_template('login.html', error=error_message)

    else:

        # Ensure username was submitted
        username = request.form.get('username').strip()
        if not username:
            return redirect(url_for('login', error='Must provide username'))

        # Ensure password was submitted
        password = request.form.get('password')
        if not password:
            return redirect(url_for('login', error='Must provide password'))

        # Query database for username
        user = db.session.query(Users).filter_by(username=username.strip()).first()

        # Ensure username exists and password is correct
        if user is None:
            return redirect(url_for('login', error='Invalid username'))

        elif not check_password_hash(user.hash, password):
            return redirect(url_for('login', error='Invalid password'))
        
        # Remember which user has logged in
        session['user_id'] = user.id

        # Redirect user to home page
        return redirect(url_for('index'))

@app.route('/logout')
def logout():

    # Forget any user_id
    session.clear()

    # Redirect user to login page
    return redirect(url_for('index'))

@app.route('/quote', methods=['GET', 'POST'])
@login_required
def quote():

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':
        return render_template('quote.html')

     # User reached route via POST (as by submitting a form via POST)
    else:

        # Ensure symbol was submitted
        symbol = request.form.get('symbol')
        if not symbol:
            flash('Must provide a stock symbol', 'warning')
            return redirect(url_for('quote'))

        # Ensure symbol is valid
        quote = lookup(symbol)
        if quote == None:
            flash('Invalid symbol', 'warning')
            return redirect(url_for('quote'))

        # Redirect user to quoted page
        return render_template('quoted.html', quote=quote)

@app.route('/register', methods=['GET', 'POST'])
def register():

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':
        return render_template('register.html')

    # User reached route via POST (as by submitting a form via POST)
    else:

        # Query database to cehck if username already exist in database
        username = request.form.get('username')
        password = request.form.get('password')
        confirmation = request.form.get('confirmation')

        # Ensure username was submitted
        if not username:
            flash('Must provide username', 'warning')
            return redirect(url_for('register'))


        # Ensure password and confirmation were submitted and match
        if not password:
            flash('Must provide password', 'warning')
            return redirect(url_for('register'))

        if not confirmation:
            flash('Must provide a confirmation password', 'warning')
            return redirect(url_for('register'))

        if password != confirmation:
            flash('Passwords do not match', 'warning')
            return redirect(url_for('register'))

        # Define password hashed
        password_hashed = generate_password_hash(password)
        
        # Query for username if already exist in database
        user = db.session.query(Users).filter_by(username=username.strip()).first()
        if user is not None:
            flash('Username already exists', 'warning')
            return redirect(url_for('register'))

        # Declare new user
        new_user = Users(
            username=username.strip(),
            hash=password_hashed,
        )

        # Add (insert) and commit new username (name and password hassed) into users table
        db.session.add(new_user)
        db.session.commit()

        # Define user id of session new user
        session['user_id'] = new_user.id

        # Display confirmation message
        flash('Registered!', 'success')

        # Redirect user to homepage
        return redirect(url_for('index'))

@app.route('/sell', methods=['GET', 'POST'])
@login_required
def sell():

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':

        # Get user id
        user_id = session['user_id']

        # Query user symbols where the total shares are > 0
        user_stocks = (
            db.session.query(Transactions.stock)
            .filter_by(user_id=user_id)
            .group_by(Transactions.stock)
            .having(func.sum(Transactions.shares)> 0)
            .all()
        )

        # Redirect user to sell file
        return render_template('sell.html', symbols=[stock[0] for stock in user_stocks])
    
    # User reached route via POST (as by submitting a form via POST)
    else:

        # Ensure symbol was submitted
        symbol = request.form.get('symbol')
        if not symbol:
            flash('Must provide stock symbol', 'warning')
            return redirect(url_for('sell'))

        # Ensure shares are a positive number
        shares = request.form.get('shares')
        if not shares or not int(shares) or int(shares) <= 0:
            flash('Shares must be a positive integer number', 'warning')
            return redirect(url_for('sell'))

        # Get user id
        user_id = session['user_id']

        # Declare stock (call lookup function with input symbol)
        stock = lookup(symbol)

        # Query total user shares of selected stock
        user_shares_db = db.session.query(
            func.sum(Transactions.shares).label('shares')
        ).filter(
            Transactions.user_id == user_id,
            Transactions.stock == stock['symbol']
        ).group_by(Transactions.stock).first()

        # Handle if user has shares
        if not user_shares_db:
            flash('Shares not found', 'warning')
            return redirect(url_for('sell'))
        
        # Declare user shares 
        user_shares = int(user_shares_db.shares)

        # Return error if shares to sell are bigger than user shares
        if int(shares) > user_shares or not shares:
            flash('Not enough funds', 'warning')
            return redirect(url_for('sell'))

        # Declare total purchase value and users total cash
        transaction_value = int(shares) * stock['price']
        user_cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
        user_cash = user_cash_db.cash

        # Update users cash
        cash_updated = user_cash + transaction_value
        db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})

        # Declare new transaction
        new_transaction = Transactions(
            user_id=user_id,
            stock=symbol,
            price=stock['price'],
            shares=(-1) * int(shares),
            date=datetime.datetime.now()
        )

        # Add (insert) and commit new transaction
        db.session.add(new_transaction)
        db.session.commit()

        # Display confirmation message
        flash('Transaction successful!', 'success')

        # Redirect user to homepage
        return redirect(url_for('index'))

@app.route('/cash', methods=['GET', 'POST'])
@login_required
def cash():

    # Get user id
    user_id = session['user_id']

    cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
    cash = round(cash_db.cash, 2)

    # User reached route via GET (as by clicking a link or via redirect)
    if request.method == 'GET':

        # Redirect user to cash file
        return render_template('cash.html', cash=cash)

    # User reached route via POST (as by submitting a form via POST)
    else:

        # Ensure cash added is a positive number
        cash_added = float(request.form.get('add_cash'))
        if not cash_added or cash_added < 0:
            flash('Must provide cash or cash must be a positive number', 'warning')
            return redirect(url_for('cash'))

        # Declare and update cash updated to user cash
        cash_updated = round(cash + cash_added, 2)
        db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})

        # Commit changes in user cash
        db.session.commit()

        # Display confirmation message
        flash('Transaction successful!', 'success')

        # Redirect to cash page
        return redirect(url_for('index'))
