from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import firestore as admin_firestore
from datetime import datetime, timedelta, timezone
import os
import base64
import json
import time


app = Flask(__name__)


# Enable CORS for frontend
CORS(app)


# ============================================================
# Configuration
# ============================================================


WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', 'EAAQnezZAE2U4BP2TbN9TOdX4pDseOr7APww3HsDAcZCbT1ZBnBac9bCe5Qz7eVFITvcZBOBa1ibHb39dPnsKCvdSppdVALaEM2bcn5cNpDTnX4iOtVwOE7QZC9P59xLhTC4aQ2Kwz2Lfl9792jC8ywvrRLK3PxFNN1czlDlHRcZBZCVLi27QzrioYVKAUtmJwZDZD')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '913694458491714')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'zest_rewards_webhook_2025')
RESTAURANT_ID = os.environ.get('RESTAURANT_ID', 'rest_001')


# ============================================================
# Initialize Firebase
# ============================================================


try:
    firebase_creds_base64 = os.environ.get('FIREBASE_CREDENTIALS_BASE64')
    
    if firebase_creds_base64:
        print("🔐 Using Firebase credentials from environment variable")
        cred_json = base64.b64decode(firebase_creds_base64)
        cred_dict = json.loads(cred_json)
        cred = credentials.Certificate(cred_dict)
    else:
        print("📁 Using Firebase credentials from file")
        cred = credentials.Certificate("firebase-credentials.json")
    
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase connected!")
except Exception as e:
    print(f"❌ Firebase error: {e}")
    db = None


# ============================================================
# Helper Functions
# ============================================================


def clean_phone_number(phone):
    """Remove + sign and clean phone number"""
    if not phone:
        return None
    cleaned = phone.replace('+', '').replace(' ', '').replace('-', '')
    return cleaned





# ============================================================
# WhatsApp Functions (Single WABA)
# ============================================================


def send_text(to_number, message, restaurant_id=None):
    """
    Send WhatsApp text message using global credentials.
    
    Args:
        to_number: Customer phone number
        message: Text message body
        restaurant_id: For logging/audit only (not used for credentials)
    
    Returns:
        Dict with response data or error
    """
    clean_number = clean_phone_number(to_number)
    
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "text",
        "text": {"body": message}
    }
    
    # Retry logic: 3 attempts with exponential backoff
    max_retries = 3
    base_delay = 1  # seconds
    
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            print(f"📤 [Attempt {attempt + 1}] Sent to {clean_number} (restaurant: {restaurant_id}): {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                return result
            
            # Log error
            print(f"❌ WhatsApp API Error: {response.text}")
            
            # Don't retry client errors (4xx except rate limits)
            if 400 <= response.status_code < 500 and response.status_code != 429:
                return {"error": response.text}
            
            # Retry on 5xx or 429 (rate limit)
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"⏳ Retrying in {delay}s...")
                time.sleep(delay)
            else:
                return {"error": response.text}
                
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                return {"error": "Request timeout"}
                
        except requests.exceptions.RequestException as e:
            print(f"🔌 Network error: {e}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                return {"error": f"Network error: {str(e)}"}
    
    return {"error": "Max retries exceeded"}


def send_template_message(phone_number, template_name, params):
    """
    Send WhatsApp template message (no 24-hour limit)
    """
    clean_number = clean_phone_number(phone_number)
    
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params]
                }
            ]
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    print(f"[TEMPLATE SEND] → {clean_number}: {response.status_code}\n{response.text}\n")
    return response.json()



# ============================================================
# Campaign Functions
# ============================================================


def get_customers_by_segment(segment, restaurant_id=None):
    """
    Get customers based on segment type
    
    Segments:
    - all: All customers
    - vip: Customers with 500+ points
    - inactive: Haven't visited in 30+ days
    - active: Visited in last 30 days
    - high_points: 200+ points
    """
    if not db:
        return []
    
    rest_id = restaurant_id or RESTAURANT_ID
    
    try:
        if segment == 'vip':
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('points_balance', '>=', 500)\
                .stream()
                
        elif segment == 'inactive':
            thirty_days_ago = datetime.now() - timedelta(days=30)
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('last_visit', '<', thirty_days_ago)\
                .stream()
                
        elif segment == 'active':
            thirty_days_ago = datetime.now() - timedelta(days=30)
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('last_visit', '>=', thirty_days_ago)\
                .stream()
                
        elif segment == 'high_points':
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('points_balance', '>=', 200)\
                .stream()
                
        else:  # 'all' or default
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .stream()
        
        # Convert to list
        customers = []
        for customer in customers_ref:
            customers.append(customer.to_dict())
        
        return customers
        
    except Exception as e:
        print(f"❌ Error getting customers: {e}")
        return []


def personalize_message(message, customer_data):
    """Replace placeholders with customer data"""
    personalized = message
    
    # Replace placeholders
    personalized = personalized.replace('{name}', customer_data.get('customer_name', 'Valued Customer'))
    personalized = personalized.replace('{points}', str(customer_data.get('points_balance', 0)))
    personalized = personalized.replace('{visits}', str(customer_data.get('total_visits', 0)))
    
    return personalized


# ============================================================
# Firebase Functions
# ============================================================


def get_customer(phone_number, restaurant_id):
    """Get customer from Firestore"""
    if not db:
        return None
    
    phone = clean_phone_number(phone_number)
    customer_id = f"{phone}_{restaurant_id}"
    customer_ref = db.collection('customers').document(customer_id)
    customer = customer_ref.get()
    
    if customer.exists:
        print(f"✅ Customer found: {customer_id}")
        return customer.to_dict()
    
    print(f"❌ Customer not found: {customer_id}")
    return None

# ============================================================
# Onboarding Helper Functions - ADD THESE
# ============================================================

def get_customer_by_phone_only(phone_number, restaurant_id):
    """Get customer by phone and restaurant"""
    if not db:
        return None
    
    phone = clean_phone_number(phone_number)
    customer_id = f"{phone}_{restaurant_id}"
    customer_ref = db.collection('customers').document(customer_id)
    customer = customer_ref.get()
    
    if customer.exists:
        print(f"✅ Found existing customer: {customer_id}")
        return customer.to_dict()
    
    print(f"ℹ️ No customer found: {customer_id}")
    return None


def get_restaurant_code(restaurant_id):
    """Get active signup code for restaurant"""
    if not db:
        print("❌ Database not connected")
        return None
    
    try:
        rest_doc = db.collection('restaurant_codes').document(restaurant_id).get()
        
        if rest_doc.exists:
            code = rest_doc.to_dict().get('active_code')
            print(f"✅ Restaurant code found: {code}")
            return code
        else:
            print(f"⚠️ No code set for restaurant: {restaurant_id}")
            return None
    except Exception as e:
        print(f"❌ Error getting restaurant code: {e}")
        return None


def validate_signup_code(code_entered, restaurant_id):
    """Check if entered code matches restaurant's active code"""
    active_code = get_restaurant_code(restaurant_id)
    
    if not active_code:
        return False, "No active code set for this restaurant"
    
    if code_entered.upper().strip() != active_code.upper().strip():
        print(f"❌ Code mismatch: entered '{code_entered}' vs active '{active_code}'")
        return False, "Invalid code"
    
    print(f"✅ Code validated: {code_entered}")
    return True, "Valid"


def create_onboarding_customer(phone_number, code, restaurant_id):
    """Create new customer during onboarding"""
    if not db:
        print("❌ Database not connected")
        return False
    
    now = datetime.now(timezone.utc)
    phone_clean = clean_phone_number(phone_number)
    customer_id = f"{phone_clean}_{restaurant_id}"
    
    try:
        db.collection('customers').document(customer_id).set({
            'phone_number': phone_clean,
            'customer_name': None,
            'restaurant_id': restaurant_id,
            'registered_at': now,
            'last_visit': now,
            'signup_code': code,
            'status': 'active',
            'awaiting_name': False,  # Not collecting names
            'onboarding_source': 'QR_CODE',
            'points_balance': 0,
            'total_points_earned': 0,
            'total_visits': 0
        })
        
        # Increment signup counter
        db.collection('restaurant_codes').document(restaurant_id).update({
            'total_signups': admin_firestore.Increment(1)
        })
        
        print(f"✅ Created onboarding customer: {customer_id}")
        return True
        
    except Exception as e:
        print(f"❌ Error creating customer: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_customer_name(phone_number, restaurant_id, name):
    """Save customer name after onboarding"""
    if not db:
        print("❌ Database not connected")
        return False
    
    phone_clean = clean_phone_number(phone_number)
    customer_id = f"{phone_clean}_{restaurant_id}"
    
    try:
        db.collection('customers').document(customer_id).update({
            'customer_name': name,
            'awaiting_name': False,
            'name_captured_at': datetime.now(timezone.utc)
        })
        
        print(f"✅ Saved name for {customer_id}: {name}")
        return True
        
    except Exception as e:
        print(f"❌ Error saving name: {e}")
        import traceback
        traceback.print_exc()
        return False


# ============================================================
# Flask Routes - General
# ============================================================


@app.route('/')
def home():
    return "✅ Feastly API is running!"


# ============================================================
# Flask Routes - Transaction API
# ============================================================


@app.route('/create-transaction', methods=['POST'])
def create_transaction():
    """Create transaction from cashier frontend"""
    print("\n" + "="*60)
    print("📥 CREATE TRANSACTION REQUEST RECEIVED")
    print("="*60)
    
    data = request.get_json()
    print(f"📦 Received data: {data}")
    
    if not data:
        print("❌ No JSON data received")
        return jsonify({"status": "error", "error": "No data provided"}), 400
    
    try:
        transaction_id = data.get('transaction_id')
        customer_phone = clean_phone_number(data.get('customer_phone'))
        customer_name = data.get('customer_name', '').strip() or data.get('cashier_name', '').strip()
        restaurant_id = data.get('restaurant_id', RESTAURANT_ID)
        bill_amount = data.get('bill_amount')
        points_earned = data.get('points_earned')
        
        print(f"✓ Transaction ID: {transaction_id}")
        print(f"✓ Phone (cleaned): {customer_phone}")
        print(f"✓ Customer Name: {customer_name or 'Not provided'}")
        print(f"✓ Restaurant ID: {restaurant_id}")
        print(f"✓ Bill: {bill_amount}")
        print(f"✓ Points: {points_earned}")
        
        if not all([transaction_id, customer_phone, bill_amount, points_earned]):
            print("❌ Missing required fields")
            return jsonify({"status": "error", "error": "Missing required fields"}), 400
        
        now = datetime.now(timezone.utc)
        
        # Save transaction
        print(f"💾 Saving transaction to Firebase...")
        db.collection('transactions').document(transaction_id).set({
            'transaction_id': transaction_id,
            'customer_phone': customer_phone,
            'restaurant_id': restaurant_id,
            'bill_amount': float(bill_amount),
            'points_earned': int(points_earned),
            'created_at': now,
            'claimed_at': now
        })
        print(f"✅ Transaction saved: {transaction_id}")
        
        # Determine expiry days
        expiry_days = 90
        try:
            rest_snap = db.collection('restaurants').document(restaurant_id).get()
            if rest_snap.exists:
                expiry_days = int(rest_snap.to_dict().get('points_expiry_days', expiry_days))
        except Exception as e:
            print("Warning reading restaurant expiry:", e)
        
        expires_at = now + timedelta(days=expiry_days)
        
        # Create point_event
        point_event_id = f"pe_{transaction_id}"
        db.collection('point_events').document(point_event_id).set({
            "points": int(points_earned),
            "remaining": int(points_earned),
            "customer_phone": customer_phone,
            "restaurant_id": restaurant_id,
            "created_at": now,
            "expires_at": expires_at,
            "expired": False,
            "status": "active",
            "transaction_id": transaction_id
        })
        print(f"✅ Point event created: {point_event_id}, expires: {expires_at.isoformat()}")
        
        # Update or create customer
        print(f"👤 Updating customer profile...")
        customer_id = f"{customer_phone}_{restaurant_id}"
        customer_ref = db.collection('customers').document(customer_id)
        customer_snap = customer_ref.get()
        
        if customer_snap.exists:
            print(f"  → Customer exists, adding points...")
            current = customer_snap.to_dict()
            
            update_data = {
                'points_balance': current.get('points_balance', 0) + int(points_earned),
                'total_points_earned': current.get('total_points_earned', 0) + int(points_earned),
                'total_visits': current.get('total_visits', 0) + 1,
                'last_visit': now
            }
            
            if customer_name and not current.get('customer_name'):
                update_data['customer_name'] = customer_name
                print(f"  → Setting customer name: {customer_name}")
            
            customer_ref.update(update_data)
            print(f"  ✅ Customer updated: +{points_earned} points added")
        else:
            print(f"  → New customer, creating with {points_earned} points...")
            customer_ref.set({
                'phone_number': customer_phone,
                'customer_name': customer_name,
                'restaurant_id': restaurant_id,
                'points_balance': int(points_earned),
                'total_points_earned': int(points_earned),
                'total_visits': 1,
                'registered_at': now,
                'last_visit': now
            })
            print(f"  ✅ New customer created: {customer_name or customer_phone}")
        
        print("="*60)
        print("✅ SUCCESS: Transaction completed and points added")
        print("="*60 + "\n")
        
        return jsonify({
            "status": "ok",
            "message": "Transaction created successfully",
            "transaction_id": transaction_id,
            "point_event_id": point_event_id,
            "expires_at": expires_at.isoformat()
        }), 200
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        print("="*60 + "\n")
        return jsonify({"status": "error", "error": str(e)}), 500


# ============================================================
# Flask Routes - Redemption API
# ============================================================


@app.route('/redeem', methods=['POST'])
def redeem_points():
    """
    Body JSON:
    {
      "customer_phone": "919876543210",
      "points_to_redeem": 100,
      "reward_description": "Redeem points"
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"status":"error","error":"No JSON payload received"}), 400

    customer_phone = clean_phone_number(data.get('customer_phone'))
    try:
        points_to_redeem = int(data.get('points_to_redeem', 0))
    except Exception:
        return jsonify({"status":"error","error":"points_to_redeem must be an integer"}), 400

    if not customer_phone:
        return jsonify({"status":"error","error":"customer_phone required"}), 400
    if points_to_redeem <= 0:
        return jsonify({"status":"error","error":"points_to_redeem must be > 0"}), 400

    reward_description = data.get('reward_description') or "Redeemed loyalty points"
    restaurant_id = data.get('restaurant_id') or RESTAURANT_ID

    customer_id = f"{customer_phone}_{restaurant_id}"
    customer_ref = db.collection('customers').document(customer_id)
    counter_ref = db.collection('counters').document('redemption_counter')

    transaction = db.transaction()

    @firestore.transactional
    def _txn_redeem(transaction):
        # ========================================
        # PHASE 1: ALL READS MUST COME FIRST
        # ========================================
        
        # Read 1: Get customer
        cust_snap = customer_ref.get(transaction=transaction)
        if not cust_snap.exists:
            raise ValueError("Customer not found")

        cust = cust_snap.to_dict()
        current_balance = int(cust.get('points_balance', 0))

        # Validate balance
        if points_to_redeem > current_balance:
            raise ValueError(f"Insufficient points. Available: {current_balance}")

        # Read 2: Get counter BEFORE incrementing
        counter_snap = counter_ref.get(transaction=transaction)
        if counter_snap.exists:
            current_count = int(counter_snap.get('count') or 0)
            new_count = current_count + 1
        else:
            new_count = 1

        # Read 3: Get all point events
        pe_query = db.collection('point_events')\
                     .where('customer_phone', '==', customer_phone)\
                     .where('restaurant_id', '==', restaurant_id)\
                     .where('status', 'in', ['active', 'partial'])\
                     .order_by('expires_at')\
                     .limit(200)

        point_events_to_update = []
        for pe_doc in pe_query.stream():
            pe_data = pe_doc.to_dict()
            point_events_to_update.append({
                'ref': pe_doc.reference,
                'id': pe_doc.id,
                'data': pe_data
            })

        # ========================================
        # PHASE 2: PROCESS DATA (NO DB OPS)
        # ========================================
        
        remaining_to_consume = points_to_redeem
        consumed_events = []

        for pe_item in point_events_to_update:
            if remaining_to_consume <= 0:
                break

            pe_data = pe_item['data']
            avail = int(pe_data.get('remaining', pe_data.get('points', 0) or 0))
            
            if avail <= 0:
                continue

            use = min(avail, remaining_to_consume)
            new_remaining = avail - use

            # Store update info
            pe_item['use'] = use
            pe_item['new_remaining'] = new_remaining
            
            consumed_events.append({
                "point_event_id": pe_item['id'],
                "used": use
            })
            
            remaining_to_consume -= use

        # Handle synthetic event if needed
        if remaining_to_consume > 0:
            print(f"⚠️ WARNING: {remaining_to_consume} points not found in events, but customer has {current_balance} balance")
            consumed_events.append({
                "point_event_id": "synthetic_balance_correction",
                "used": remaining_to_consume
            })

        # ========================================
        # PHASE 3: ALL WRITES COME LAST
        # ========================================
        
        now = datetime.now(timezone.utc)

        # Write 1: Update counter
        if counter_snap.exists:
            transaction.update(counter_ref, {'count': new_count})
        else:
            transaction.set(counter_ref, {'count': new_count})

        # Write 2: Update point events
        for pe_item in point_events_to_update:
            if 'use' not in pe_item:
                continue
                
            pe_ref = pe_item['ref']
            new_remaining = pe_item['new_remaining']
            
            if new_remaining == 0:
                transaction.update(pe_ref, {
                    'remaining': 0,
                    'status': 'redeemed',
                    'redeemed_at': now
                })
            else:
                transaction.update(pe_ref, {
                    'remaining': new_remaining,
                    'status': 'partial',
                    'redeemed_at': now
                })

        # Write 3: Create redemption record
        redemption_id = f"R{new_count:04d}"
        
        redemption_doc = {
            "redemption_id": redemption_id,
            "customer_phone": customer_phone,
            "points_redeemed": int(points_to_redeem),
            "reward_description": reward_description,
            "restaurant_id": restaurant_id,
            "consumed_events": consumed_events,
            "status": "completed",
            "created_at": now,
            "completed_at": now
        }

        redemption_ref = db.collection('redemptions').document(redemption_id)
        transaction.set(redemption_ref, redemption_doc)

        # Write 4: Update customer
        new_balance = current_balance - int(points_to_redeem)

        update_data = {
            "points_balance": new_balance,
            "last_redeemed_at": now,
            "total_points_redeemed": admin_firestore.Increment(int(points_to_redeem))
        }
        transaction.update(customer_ref, update_data)

        return {
            "redemption_id": redemption_id,
            "new_balance": new_balance,
            "created_at": now.isoformat()
        }

    try:
        result = _txn_redeem(transaction)
        return jsonify({
            "status": "ok",
            "message": "Redeemed successfully",
            "redemption_id": result['redemption_id'],
            "new_balance": result['new_balance'],
            "created_at": result['created_at']
        }), 200

    except ValueError as ve:
        return jsonify({"status": "error", "error": str(ve)}), 400

    except Exception as e:
        print("ERROR redeem_points:", e)
        import traceback
        traceback.print_exc()
        return jsonify({"status":"error","error":str(e)}), 500


# ============================================================
# Flask Routes - Campaign API
# ============================================================


@app.route('/send-campaign', methods=['POST'])
def send_campaign():
    """
    Send campaign messages to customer segments using single WABA.
    
    Request body:
    {
        "segment": "all|vip|inactive|active|high_points",
        "message": "Campaign message with {name} and {points} placeholders",
        "restaurant_id": "rest_001" (optional)
    }
    """
    print("\n" + "="*60)
    print("📢 CAMPAIGN REQUEST RECEIVED")
    print("="*60)
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        segment = data.get('segment', 'all')
        message = data.get('message')
        restaurant_id = data.get('restaurant_id', RESTAURANT_ID)
        
        if not message:
            return jsonify({"error": "Message is required"}), 400
        
        print(f"📊 Segment: {segment}")
        print(f"💬 Message template: {message[:50]}...")
        print(f"🏪 Restaurant: {restaurant_id}")
        
        # Get customers in segment
        customers = get_customers_by_segment(segment, restaurant_id)
        print(f"👥 Found {len(customers)} customers in segment")
        
        if len(customers) == 0:
            return jsonify({
                "success": True,
                "sent_count": 0,
                "message": "No customers found in this segment"
            }), 200
        
        # Send to each customer
        sent_count = 0
        failed_count = 0
        
        for customer in customers:
            try:
                # Personalize message
                personalized_msg = personalize_message(message, customer)
                
                # Send WhatsApp message using single WABA
                result = send_text(customer['phone_number'], personalized_msg, restaurant_id)
                
                if result and not result.get('error'):
                    sent_count += 1
                    print(f"  ✅ Sent to {customer.get('customer_name', 'Customer')}")
                else:
                    failed_count += 1
                    print(f"  ❌ Failed to {customer.get('customer_name', 'Customer')}")
                    
            except Exception as e:
                failed_count += 1
                print(f"  ❌ Error sending to customer: {e}")
        
        print("="*60)
        print(f"✅ Campaign complete: {sent_count} sent, {failed_count} failed")
        print("="*60 + "\n")
        
        return jsonify({
            "success": True,
            "sent_count": sent_count,
            "failed_count": failed_count,
            "total_targeted": len(customers),
            "segment": segment,
            "message": f"Campaign sent to {sent_count} customers"
        }), 200
        
    except Exception as e:
        print(f"❌ Campaign error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
    


@app.route('/send-template-campaign', methods=['POST'])
def send_template_campaign():
    """
    Send campaign using WhatsApp TEMPLATE messages (No 24hr limit)
    with 3 variables:
    {{1}} = customer_name
    {{2}} = points_balance
    {{3}} = restaurant_name (from Firestore)
    """

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    segment = data.get("segment", "all")
    restaurant_id = data.get("restaurant_id", RESTAURANT_ID)
    template_name = data.get("template_name")

    if not template_name:
        return jsonify({"error": "template_name is required"}), 400

    # 🎯 1. Fetch restaurant name from Firestore
    rest_doc = db.collection('restaurants').document(restaurant_id).get()
    if rest_doc.exists:
        restaurant_name = rest_doc.to_dict().get('restaurant_name', "Our Restaurant")
    else:
        restaurant_name = "Our Restaurant"

    # 🎯 2. Get customers
    customers = get_customers_by_segment(segment, restaurant_id)
    total = len(customers)

    if total == 0:
        return jsonify({"success": False, "message": "No customers found"}), 200

    sent, failed = 0, 0

    for cust in customers:
        try:
            # 🎯 3. Build params dynamically for template
            params = [
                cust.get("customer_name", "Customer"),       # {{1}}
                str(cust.get("points_balance", 0)),          # {{2}}
                restaurant_name                              # {{3}}
            ]

            # 🎯 4. Send template message
            result = send_template_message(
                cust["phone_number"],
                template_name,
                params
            )

            if "messages" in result:
                sent += 1
            else:
                failed += 1

        except Exception as err:
            print("Error sending:", err)
            failed += 1

    return jsonify({
        "success": True,
        "template": template_name,
        "segment": segment,
        "restaurant_name": restaurant_name,
        "total_customers": total,
        "sent": sent,
        "failed": failed
    }), 200




# ============================================================
# Flask Routes - WhatsApp Webhook
# ============================================================


@app.route('/webhook', methods=['POST'])
def receive_message():
    """Receive messages from Meta WhatsApp - Onboarding + Balance Check"""
    data = request.get_json()

    print("=" * 60)
    print("📨 Webhook received")
    print("=" * 60)

    try:
        value = data['entry'][0]['changes'][0]['value']

        # Verify this is our phone number
        metadata = value.get('metadata', {})
        incoming_phone_id = metadata.get('phone_number_id')

        if incoming_phone_id and incoming_phone_id != PHONE_NUMBER_ID:
            print(f"⚠️ Message for different phone_number_id: {incoming_phone_id}")
            print(f"   Expected: {PHONE_NUMBER_ID}")

        if 'messages' in value:
            message = value['messages'][0]
            from_number = clean_phone_number(message['from'])

            if 'text' in message:
                text = message['text']['body']
                print(f"📱 From: {from_number}")
                print(f"💬 Message: {text}")

                text_clean = text.strip()
                text_upper = text_clean.upper()

                # ========================================
                
                # ========================================
                # PRIORITY 2: Onboarding Flow
                # ========================================
                
                print(f"🔍 Checking customer for onboarding...")
                customer = get_customer_by_phone_only(from_number, RESTAURANT_ID)
                
                if customer:
                    print(f"✅ Customer exists")
                    print(f"   awaiting_name: {customer.get('awaiting_name')}")
                    print(f"   status: {customer.get('status', 'NOT SET')}")
                    print(f"   customer_name: {customer.get('customer_name', 'NOT SET')}")
                else:
                    print(f"ℹ️ New customer - not in database")

                # ========================================
                # CASE 1: Customer awaiting name
                # ========================================
                if customer and customer.get('awaiting_name') == True:
                    print(f"👤 CASE 1: Customer awaiting name")
                    
                    name = text_clean.title()
                    
                    # Validate name length
                    if len(name) < 2 or len(name) > 30:
                        print(f"⚠️ Invalid name length: {len(name)}")
                        send_text(from_number, "Please enter a valid name (2-30 characters).", RESTAURANT_ID)
                        return jsonify({"status": "ok"}), 200
                    
                    # Save name
                    print(f"💾 Saving name: {name}")
                    if save_customer_name(from_number, RESTAURANT_ID, name):
                        message_text = f"""Perfect, {name}! ✅

You're all set! 🎊

We'll send you exclusive offers and updates soon.
Stay tuned! 📲"""
                        print("📤 Sending welcome message")
                        send_text(from_number, message_text, RESTAURANT_ID)
                        print("✅ Message sent successfully!")
                    else:
                        print("❌ Failed to save name")
                        send_text(from_number, "Sorry, something went wrong. Please try again.", RESTAURANT_ID)
                    
                    return jsonify({"status": "ok"}), 200

                # ========================================
                # CASE 2: Existing customer (already registered)
                # ========================================
                elif customer and customer.get('awaiting_name') != True:
                    print(f"✅ CASE 2: Existing registered customer")

                    # Check if they sent the signup code
                    print(f"🔐 Checking if message is signup code...")
                    is_valid_code, _ = validate_signup_code(text_clean, RESTAURANT_ID)

                    customer_name = customer.get('customer_name', 'there')

                    if is_valid_code:
                        # Check if customer already signed up with this code
                        customer_signup_code = customer.get('signup_code', '').upper()
                        entered_code = text_clean.upper()

                        if customer_signup_code == entered_code:
                            # Customer already used this code
                            print(f"ℹ️ Customer already signed up with code: {entered_code}")
                            message_text = f"""Hey {customer_name}! 👋

You're already registered with us using this code! ✅

Watch out for exclusive offers coming soon! 🎁"""
                        else:
                            # Customer entered a different valid code
                            print(f"⚠️ Customer entered different code. Their code: {customer_signup_code}, Entered: {entered_code}")
                            message_text = f"""Hey {customer_name}! 👋

This is a different signup code. You're already registered with code {customer_signup_code}.

One account per phone number! 😊"""
                    else:
                        # Not a signup code - just a random message
                        print(f"ℹ️ Customer sent random message: '{text_clean}'")
                        message_text = f"""Hey {customer_name}! 👋

Thanks for your message! 

Need help? Contact our staff or visit us soon! 😊"""

                    print("📤 Sending response to existing customer")
                    send_text(from_number, message_text, RESTAURANT_ID)
                    print("✅ Message sent successfully!")
                    return jsonify({"status": "ok"}), 200

                # ========================================
                # CASE 3: New customer - validate signup code
                # ========================================
                else:  # customer is None (new customer)
                    print(f"🆕 CASE 3: New customer attempting signup")
                    
                    # Validate signup code
                    print(f"🔐 Validating code: '{text_clean}'")
                    is_valid, validation_message = validate_signup_code(text_clean, RESTAURANT_ID)
                    print(f"   Validation result: {is_valid} - {validation_message}")
                    
                    if is_valid:
                        print(f"✅ Valid code! Creating customer...")
                        
                        # Create customer
                        if create_onboarding_customer(from_number, text_clean.upper(), RESTAURANT_ID):
                            message_text = """🎉 Welcome to our exclusive club!

You're all set! We'll send you exclusive offers and updates soon.
Stay tuned! 📲"""
                            print("📤 Sending welcome message")
                            send_text(from_number, message_text, RESTAURANT_ID)
                            print("✅ Message sent successfully!")
                        else:
                            print("❌ Failed to create customer")
                            send_text(from_number, "Sorry, registration failed. Please try again later.", RESTAURANT_ID)
                    else:
                        print(f"❌ Invalid code entered")
                        message_text = """❌ Invalid code.

Please ask the cashier for the correct signup code."""
                        print("📤 Sending invalid code message")
                        send_text(from_number, message_text, RESTAURANT_ID)
                        print("✅ Message sent successfully!")
                    
                    return jsonify({"status": "ok"}), 200

        elif 'statuses' in value:
            status = value['statuses'][0]
            print(f"📊 Status update: {status.get('status')}")

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ ERROR in webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ============================================================
# Flask Routes - Onboarding Admin API
# ============================================================

@app.route('/admin/set-code', methods=['POST'])
def set_restaurant_code():
    """
    Set or update signup code for restaurant
    Body: {
        "code": "ABC123",
        "restaurant_id": "rest_001" (optional)
    }
    """
    print("\n" + "="*60)
    print("🔑 SET CODE REQUEST")
    print("="*60)
    
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "No data provided"}), 400
        
        new_code = data.get('code', '').upper().strip()
        restaurant_id = data.get('restaurant_id', RESTAURANT_ID)
        
        if not new_code:
            return jsonify({"error": "Code is required"}), 400
        
        if len(new_code) < 4:
            return jsonify({"error": "Code must be at least 4 characters"}), 400
        
        print(f"📝 Setting code: {new_code} for restaurant: {restaurant_id}")
        
        # Set or update code
        db.collection('restaurant_codes').document(restaurant_id).set({
            'active_code': new_code,
            'updated_at': datetime.now(timezone.utc),
            'restaurant_id': restaurant_id,
            'total_signups': 0
        }, merge=True)
        
        print(f"✅ Code updated successfully")
        print("="*60 + "\n")
        
        return jsonify({
            "status": "ok",
            "message": f"Code updated to {new_code}",
            "code": new_code,
            "restaurant_id": restaurant_id
        }), 200
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/admin/get-code', methods=['GET'])
def get_current_code():
    """
    Get current signup code
    Query params: restaurant_id (optional)
    """
    try:
        restaurant_id = request.args.get('restaurant_id', RESTAURANT_ID)
        
        rest_doc = db.collection('restaurant_codes').document(restaurant_id).get()
        
        if rest_doc.exists:
            data = rest_doc.to_dict()
            return jsonify({
                'status': 'ok',
                'code': data.get('active_code'),
                'total_signups': data.get('total_signups', 0),
                'updated_at': data.get('updated_at').isoformat() if data.get('updated_at') else None
            }), 200
        else:
            return jsonify({
                'status': 'ok',
                'code': None,
                'message': 'No code set for this restaurant'
            }), 200
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/admin/onboarding-stats', methods=['GET'])
def get_onboarding_stats():
    """
    Get onboarding statistics
    Query params: restaurant_id (optional)
    """
    try:
        restaurant_id = request.args.get('restaurant_id', RESTAURANT_ID)
        
        # Get total customers
        customers = db.collection('customers')\
            .where('restaurant_id', '==', restaurant_id)\
            .where('onboarding_source', '==', 'QR_CODE')\
            .stream()
        
        total_customers = 0
        customers_with_name = 0
        customers_without_name = 0
        
        for customer in customers:
            total_customers += 1
            cust_data = customer.to_dict()
            if cust_data.get('customer_name'):
                customers_with_name += 1
            else:
                customers_without_name += 1
        
        # Get code info
        code_doc = db.collection('restaurant_codes').document(restaurant_id).get()
        current_code = None
        total_signups = 0
        
        if code_doc.exists:
            code_data = code_doc.to_dict()
            current_code = code_data.get('active_code')
            total_signups = code_data.get('total_signups', 0)
        
        return jsonify({
            'status': 'ok',
            'current_code': current_code,
            'total_customers': total_customers,
            'customers_with_name': customers_with_name,
            'customers_without_name': customers_without_name,
            'completion_rate': round((customers_with_name / total_customers * 100), 2) if total_customers > 0 else 0,
            'total_signups_tracked': total_signups
        }), 200
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ============================================================
# Flask Routes - Expiry Job
# ============================================================


@app.route('/run-expiry', methods=['POST'])
def run_expiry():
    """Expire point_events whose expires_at <= now"""
    try:
        now = datetime.now(timezone.utc)
        print(f"🕒 Running expiry job at {now.isoformat()}")
        coll = db.collection('point_events')
        page_size = 400
        expired_total = 0
        last_doc = None

        while True:
            q = coll.where('expires_at', '<=', now).order_by('expires_at').limit(page_size)
            if last_doc:
                q = q.start_after(last_doc)

            docs = list(q.stream())
            if not docs:
                break

            batch = db.batch()
            processed = 0

            for doc in docs:
                data = doc.to_dict()
                if data.get('expired', False) or int(data.get('remaining', 0) or 0) <= 0:
                    continue

                remaining_pts = int(data.get('remaining', 0) or 0)
                customer_phone = data.get('customer_phone')
                restaurant_id = data.get('restaurant_id', RESTAURANT_ID)

                doc_ref = doc.reference
                batch.update(doc_ref, {
                    'expired': True,
                    'expired_at': now,
                    'remaining': 0,
                    'status': 'expired'
                })

                if customer_phone:
                    customer_id = f"{customer_phone}_{restaurant_id}"
                    cust_ref = db.collection('customers').document(customer_id)
                    batch.update(cust_ref, {
                        'points_balance': admin_firestore.Increment(-remaining_pts),
                    })

                processed += 1
                expired_total += 1

            if processed > 0:
                batch.commit()
                print(f"  ✅ Committed {processed} expiries")

            last_doc = docs[-1]
            if len(docs) < page_size:
                break

        print(f"✅ Expiry job complete. Expired: {expired_total}")
        return jsonify({"status":"ok","expired": expired_total}), 200

    except Exception as e:
        print("❌ Error running expiry:", e)
        import traceback
        traceback.print_exc()
        return jsonify({"status":"error","error": str(e)}), 500


# ============================================================
# Run App
# ============================================================


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Feastly Backend Starting...")
    print(f"📱 Phone Number ID: {PHONE_NUMBER_ID}")
    print(f"🔐 Verify Token: {VERIFY_TOKEN}")
    print(f"🏪 Restaurant ID: {RESTAURANT_ID}")
    if db:
        print(f"🔥 Firebase: Connected ✅")
    else:
        print(f"🔥 Firebase: Not connected ❌")
    print("=" * 60)
    
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)