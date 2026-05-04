import os
import datetime

from flask import Flask, flash, redirect, render_template, url_for, request, session
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv

from helpers import login_required, lookup, parse_positive_decimal, parse_positive_int, usd


# Load environment variables from .env file
load_dotenv()

# Configure application
app = Flask(__name__)

# Set up secret key
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError('SECRET_KEY environment variable is required')

CSRFProtect(app)

# Configure session to use filesystem (instead of signed cookies)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', '0') == '1'
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
app.config['SQLALCHEMY_DATABASE_URI'] = (
    os.getenv('DATABASE_URL')
    or 'sqlite:///' + os.path.join(basedir, 'finance.db')
)
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

# Create database tables if they don't exist
with app.app_context():
    db.create_all()

# Custom filter
app.jinja_env.filters['usd'] = usd


@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    user_id = session['user_id']

    transactions_db = db.session.query(
        Transactions.stock,
        func.sum(Transactions.shares).label('shares'),
    ).filter_by(
        user_id=user_id
    ).group_by(
        Transactions.stock
    ).having(
        func.sum(Transactions.shares) > 0
    ).all()

    # Query user cash
    cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
    user_cash = round(cash_db.cash, 2)

    total_value = user_cash
    transactions_values = []

    for transaction in transactions_db:
        quote = lookup(transaction.stock)
        if quote is None:
            flash(f'Could not refresh price for {transaction.stock}', 'warning')
            continue

        stock_price = quote['price']
        stock_value = round(stock_price * transaction.shares, 2)
        total_value += stock_value

        transactions_values.append({
            'stock': transaction.stock,
            'shares': transaction.shares,
            'stock_price': stock_price,
            'stock_value': stock_value
        })

    if request.method == 'GET':
        return render_template('index.html', transactions=transactions_values,
                               user_cash=user_cash,
                               total_value=total_value)

    return redirect(url_for('index'))


@app.route('/buy', methods=['GET', 'POST'])
@login_required
def buy():
    if request.method == 'GET':
        return render_template('buy.html')

    user_id = session['user_id']

    symbol = request.form.get('symbol')
    if not symbol:
        flash('Must provide stock symbol', 'warning')
        return redirect(url_for('buy'))

    stock = lookup(symbol)
    if stock is None:
        flash('Invalid stock symbol', 'warning')
        return redirect(url_for('buy'))

    shares = parse_positive_int(request.form.get('shares'))
    if shares is None:
        flash('Shares must be a positive integer number', 'warning')
        return redirect(url_for('buy'))

    transaction_value = shares * stock['price']

    cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
    user_cash = cash_db.cash

    if user_cash < transaction_value:
        flash('Not enough funds', 'warning')
        return redirect(url_for('buy'))

    cash_updated = user_cash - transaction_value
    db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})

    new_transaction = Transactions(
        user_id=user_id,
        stock=stock['symbol'],
        price=stock['price'],
        shares=shares,
        date=datetime.datetime.now()
    )

    db.session.add(new_transaction)
    db.session.commit()

    flash('Transaction successful!', 'success')

    return redirect(url_for('index'))


@app.route('/history')
@login_required
def history():
    user_id = session['user_id']

    transactions_db = db.session.query(
        Transactions.stock,
        Transactions.price,
        Transactions.shares,
        Transactions.date,
    ).filter_by(user_id=user_id).order_by(Transactions.date.desc()).all()

    return render_template('history.html', transactions=transactions_db)


@app.route('/login', methods=['GET', 'POST'])
def login():
    session.clear()

    if request.method == 'GET':
        error_message = request.args.get('error')
        return render_template('login.html', error=error_message)

    username = (request.form.get('username') or '').strip()
    if not username:
        return redirect(url_for('login', error='Must provide username'))

    password = request.form.get('password')
    if not password:
        return redirect(url_for('login', error='Must provide password'))

    user = db.session.query(Users).filter_by(username=username).first()

    if user is None:
        return redirect(url_for('login', error='Invalid username'))

    if not check_password_hash(user.hash, password):
        return redirect(url_for('login', error='Invalid password'))

    session['user_id'] = user.id
    return redirect(url_for('index'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/quote', methods=['GET', 'POST'])
@login_required
def quote():
    if request.method == 'GET':
        return render_template('quote.html')

    symbol = request.form.get('symbol')
    if not symbol:
        flash('Must provide a stock symbol', 'warning')
        return redirect(url_for('quote'))

    quote = lookup(symbol)
    if quote is None:
        flash('Invalid symbol', 'warning')
        return redirect(url_for('quote'))

    return render_template('quoted.html', quote=quote)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'GET':
        return render_template('register.html')

    username = (request.form.get('username') or '').strip()
    password = request.form.get('password')
    confirmation = request.form.get('confirmation')

    if not username:
        flash('Must provide username', 'warning')
        return redirect(url_for('register'))

    if not password:
        flash('Must provide password', 'warning')
        return redirect(url_for('register'))

    if not confirmation:
        flash('Must provide a confirmation password', 'warning')
        return redirect(url_for('register'))

    if password != confirmation:
        flash('Passwords do not match', 'warning')
        return redirect(url_for('register'))

    password_hashed = generate_password_hash(password)
    user = db.session.query(Users).filter_by(username=username).first()
    if user is not None:
        flash('Username already exists', 'warning')
        return redirect(url_for('register'))

    new_user = Users(
        username=username,
        hash=password_hashed,
    )

    db.session.add(new_user)
    db.session.commit()

    session['user_id'] = new_user.id
    flash('Registered!', 'success')

    return redirect(url_for('index'))


@app.route('/sell', methods=['GET', 'POST'])
@login_required
def sell():
    if request.method == 'GET':
        user_id = session['user_id']
        user_stocks = (
            db.session.query(Transactions.stock)
            .filter_by(user_id=user_id)
            .group_by(Transactions.stock)
            .having(func.sum(Transactions.shares) > 0)
            .all()
        )

        return render_template('sell.html', symbols=[stock[0] for stock in user_stocks])

    symbol = request.form.get('symbol')
    if not symbol:
        flash('Must provide stock symbol', 'warning')
        return redirect(url_for('sell'))

    shares = parse_positive_int(request.form.get('shares'))
    if shares is None:
        flash('Shares must be a positive integer number', 'warning')
        return redirect(url_for('sell'))

    user_id = session['user_id']

    stock = lookup(symbol)
    if stock is None:
        flash('Invalid stock symbol', 'warning')
        return redirect(url_for('sell'))

    user_shares_db = db.session.query(
        func.sum(Transactions.shares).label('shares')
    ).filter(
        Transactions.user_id == user_id,
        Transactions.stock == stock['symbol']
    ).group_by(Transactions.stock).first()

    if not user_shares_db:
        flash('Shares not found', 'warning')
        return redirect(url_for('sell'))

    user_shares = int(user_shares_db.shares)
    if shares > user_shares:
        flash('Not enough shares', 'warning')
        return redirect(url_for('sell'))

    transaction_value = shares * stock['price']
    user_cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
    user_cash = user_cash_db.cash
    cash_updated = user_cash + transaction_value
    db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})

    new_transaction = Transactions(
        user_id=user_id,
        stock=stock['symbol'],
        price=stock['price'],
        shares=(-1) * shares,
        date=datetime.datetime.now()
    )

    db.session.add(new_transaction)
    db.session.commit()

    flash('Transaction successful!', 'success')
    return redirect(url_for('index'))


@app.route('/cash', methods=['GET', 'POST'])
@login_required
def cash():
    user_id = session['user_id']

    cash_db = db.session.query(Users.cash).filter_by(id=user_id).first()
    cash = round(cash_db.cash, 2)

    if request.method == 'GET':
        return render_template('cash.html', cash=cash)

    cash_added = parse_positive_decimal(request.form.get('add_cash'))
    if cash_added is None:
        flash('Must provide cash or cash must be a positive number', 'warning')
        return redirect(url_for('cash'))

    cash_updated = round(cash + float(cash_added), 2)
    db.session.query(Users).filter_by(id=user_id).update({'cash': cash_updated})

    db.session.commit()

    flash('Transaction successful!', 'success')
    return redirect(url_for('index'))
