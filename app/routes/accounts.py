from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from app import db
from app.models.account import Account
from app.models.user import User
from app.models.transaction import Transaction
from app.utils.validators import error_response
from app.utils.account_utils import generate_account_number, generate_unique_account_number
from datetime import datetime
from sqlalchemy import or_, and_
import logging
import re

bp = Blueprint('accounts', __name__, url_prefix='/api/accounts')

# Configure rate limiting - will be properly attached to the app when Blueprint is registered
limiter = Limiter(key_func=get_remote_address)

MAX_ACCOUNTS = 2
VALID_ACCOUNT_TYPES = ['checking', 'savings', 'credit']
MIN_ACCOUNT_NAME_LENGTH = 3
MAX_ACCOUNT_NAME_LENGTH = 90
MIN_BALANCE = -50.0

# Set up logging
logger = logging.getLogger(__name__)

# Sanitize input to prevent injection
def sanitize_input(text):
    if text is None:
        return None
    # Remove potentially dangerous characters
    return re.sub(r'[;\'\"\<\>]', '', str(text))

@bp.route('', methods=['GET'])
@jwt_required()
@limiter.limit("30 per minute")
def get_accounts():
    """Get all active accounts for the authenticated user"""
    try:
        user_id = int(get_jwt_identity())
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        account_type = sanitize_input(request.args.get('type'))
        
        if page < 1 or per_page < 1 or per_page > 100:
            return error_response('Invalid pagination parameters', 400)
        
        query = Account.query.filter(Account.user_id == user_id, Account.is_active == True)
        
        if account_type and account_type.lower() in VALID_ACCOUNT_TYPES:
            query = query.filter(Account.account_type == account_type.lower())
        
        paginated_accounts = query.paginate(page=page, per_page=per_page, error_out=False)
        
        accounts_data = []
        for account in paginated_accounts.items:
            account_dict = account.to_dict()
            account_dict['category'] = account_dict.pop('account_type')
            account_dict['label'] = account_dict.pop('account_name')
            account_dict['balance'] = round(float(account_dict['balance']), 2)
            accounts_data.append(account_dict)
        
        return jsonify({
            'accounts': accounts_data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': paginated_accounts.total,
                'pages': paginated_accounts.pages
            }
        })
    except Exception as e:
        logger.error(f"Error fetching accounts: {str(e)}")
        return error_response('Failed to fetch accounts', 500)

@bp.route('/<int:account_id>', methods=['GET'])
@jwt_required()
@limiter.limit("30 per minute")
def get_account(account_id):
    """Get details of a specific account"""
    try:
        user_id = int(get_jwt_identity())
        
        account = Account.query.filter(
            and_(
                Account.id == account_id,
                Account.user_id == user_id,
                Account.is_active == True
            )
        ).first()
        
        if not account:
            return error_response('Account not found', 404)
        
        account_data = account.to_dict()
        account_data['balance'] = round(float(account_data['balance']), 2)
        
        return jsonify({
            'account': account_data
        })
    except Exception as e:
        logger.error(f"Error fetching account {account_id}: {str(e)}")
        return error_response('Failed to fetch account details', 500)

@bp.route('', methods=['POST'])
@jwt_required()
@limiter.limit("5 per minute")
def create_account():
    """Create a new account for the authenticated user"""
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data:
            return error_response('No data provided', 400)
        
        # Validate input data
        account_type = sanitize_input(data.get('account_type', '')).lower()
        if not account_type or account_type not in VALID_ACCOUNT_TYPES:
            return error_response(f'Invalid account type. Must be one of: {", ".join(VALID_ACCOUNT_TYPES)}', 400)
        
        account_name = sanitize_input(data.get('account_name', '')).strip()
        if not account_name or len(account_name) < MIN_ACCOUNT_NAME_LENGTH or len(account_name) > MAX_ACCOUNT_NAME_LENGTH:
            return error_response(f'Account name must be between {MIN_ACCOUNT_NAME_LENGTH} and {MAX_ACCOUNT_NAME_LENGTH} characters', 400)
        
        try:
            initial_balance = float(data.get('initial_balance', 0.0))
            if initial_balance < MIN_BALANCE:
                return error_response(f'Initial balance cannot be less than {MIN_BALANCE}', 400)
        except (ValueError, TypeError):
            return error_response('Initial balance must be a valid number', 400)
        
        # Check account limit
        account_count = Account.query.filter_by(user_id=user_id, is_active=True).count()
        if account_count >= MAX_ACCOUNTS:
            return error_response(f'Maximum of {MAX_ACCOUNTS} accounts allowed per user', 400)
        
        # Generate unique account number
        account_number = generate_unique_account_number(user_id)
        
        # Create new account
        new_account = Account(
            account_number=account_number,
            account_type=account_type,
            account_name=account_name,
            description=sanitize_input(data.get('description', '')),
            balance=initial_balance,
            user_id=user_id
        )
        
        db.session.add(new_account)
        db.session.commit()
        
        account_data = new_account.to_dict()
        account_data['balance'] = round(float(account_data['balance']), 2)
        
        return jsonify({
            'account': account_data,
            'message': 'Account created successfully'
        }), 201
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating account: {str(e)}")
        return error_response('Failed to create account', 500)

@bp.route('/<int:account_id>', methods=['PUT'])
@jwt_required(fresh=True)
@limiter.limit("10 per minute")
def update_account(account_id):
    """Update an existing account"""
    try:
        user_id = int(get_jwt_identity())
        data = request.get_json()
        
        if not data:
            return error_response('No data provided', 400)
        
        account = Account.query.filter(
            and_(
                Account.id == account_id,
                Account.user_id == user_id,
                Account.is_active == True
            )
        ).first()
        
        if not account:
            return error_response('Account not found or access denied', 404)
        
        # Update account name if provided
        if 'account_name' in data:
            new_name = sanitize_input(data['account_name']).strip()
            if not new_name or len(new_name) < MIN_ACCOUNT_NAME_LENGTH or len(new_name) > MAX_ACCOUNT_NAME_LENGTH:
                return error_response(f'Account name must be between {MIN_ACCOUNT_NAME_LENGTH} and {MAX_ACCOUNT_NAME_LENGTH} characters', 400)
            account.account_name = new_name
        
        # Update description if provided
        if 'description' in data:
            account.description = sanitize_input(data['description'])
        
        db.session.commit()
        
        account_data = account.to_dict()
        account_data['balance'] = round(float(account_data['balance']), 2)
        
        return jsonify({
            'account': account_data,
            'message': 'Account updated successfully'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating account {account_id}: {str(e)}")
        return error_response('Failed to update account', 500)

@bp.route('/<int:account_id>', methods=['DELETE'])
@jwt_required(fresh=True)
@limiter.limit("5 per minute")
def delete_account(account_id):
    """Deactivate an account (soft delete)"""
    try:
        user_id = int(get_jwt_identity())
        
        account = Account.query.filter(
            and_(
                Account.id == account_id,
                Account.user_id == user_id,
                Account.is_active == True
            )
        ).first()
        
        if not account:
            return error_response('Account not found or access denied', 404)
        
        # Check if account has a balance
        if account.balance != 0:
            return error_response('Cannot delete account with non-zero balance', 400)
        
        account.is_active = False
        account.deactivated_at = datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'message': 'Account deactivated successfully'
        }), 200
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deactivating account {account_id}: {str(e)}")
        return error_response('Failed to deactivate account', 500)

@bp.route('/<int:account_id>/transactions', methods=['GET'])
@jwt_required()
@limiter.limit("30 per minute")
def get_account_transactions(account_id):
    """Get transactions for a specific account"""
    try:
        user_id = int(get_jwt_identity())
        
        # Verify account exists and belongs to user
        account = Account.query.filter(
            and_(
                Account.id == account_id,
                Account.user_id == user_id,
                Account.is_active == True
            )
        ).first()
        
        if not account:
            return error_response('Account not found', 404)
        
        # Build query
        query = Transaction.query.filter(
            or_(
                Transaction.from_account_id == account_id,
                Transaction.to_account_id == account_id
            )
        )
        
        # Date filtering
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(Transaction.timestamp >= start_date)
            except ValueError:
                return error_response('Invalid start_date format. Use YYYY-MM-DD', 400)
        
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date.replace(hour=23, minute=59, second=59)
                query = query.filter(Transaction.timestamp <= end_date)
            except ValueError:
                return error_response('Invalid end_date format. Use YYYY-MM-DD', 400)
        
        # Transaction type filtering
        tx_type = sanitize_input(request.args.get('type'))
        if tx_type:
            tx_type = tx_type.lower()
            if tx_type == 'deposit':
                query = query.filter(
                    Transaction.to_account_id == account_id
                )
            elif tx_type == 'withdrawal':
                query = query.filter(
                    Transaction.from_account_id == account_id
                )
            elif tx_type == 'transfer':
                query = query.filter(Transaction.transaction_type == 'transfer')
        
        # Amount filtering
        min_amount = request.args.get('min_amount', type=float)
        max_amount = request.args.get('max_amount', type=float)
        
        if min_amount is not None:
            query = query.filter(Transaction.amount >= min_amount)
        
        if max_amount is not None:
            query = query.filter(Transaction.amount <= max_amount)
        
        # Search filtering
        search = sanitize_input(request.args.get('search'))
        if search:
            search_term = f'%{search}%'
            query = query.filter(Transaction.description.ilike(search_term))
        
        # Pagination
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        
        if page < 1 or per_page < 1 or per_page > 100:
            return error_response('Invalid pagination parameters', 400)
        
        # Execute query
        paginated_transactions = query.order_by(Transaction.timestamp.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        transactions = []
        for tx in paginated_transactions.items:
            tx_dict = tx.to_dict()
            
            # Format transaction amount for display
            tx_dict['amount'] = round(float(tx_dict['amount']), 2)
            
            # Add direction indicator for the account
            if tx.from_account_id == account_id:
                tx_dict['direction'] = 'outgoing'
            else:
                tx_dict['direction'] = 'incoming'
                
            transactions.append(tx_dict)
        
        return jsonify({
            'transactions': transactions,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': paginated_transactions.total,
                'pages': paginated_transactions.pages
            }
        })
    except Exception as e:
        logger.error(f"Error fetching transactions for account {account_id}: {str(e)}")
        return error_response('Failed to fetch transactions', 500)

def init_app(app):
    """Initialize the blueprint with the Flask app"""
    # Properly attach the limiter to the app
    limiter.init_app(app)
    app.register_blueprint(bp)