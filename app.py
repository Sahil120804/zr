from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import firestore as admin_firestore
from datetime import datetime, timedelta
import os
import base64
import json

app = Flask(__name__)

# Enable CORS for frontend
CORS(app)

# ============================================================
# Configuration
# ============================================================

WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', 'EAAQnezZAE2U4BPk6F39fdDbF4NzIYZCPffdBL9qIZAJabVBPVd0F5qigPnY7U0zrasRNCjNM62IW4UsEEquZAUCNo0YwXx6uTO47mlZAfbMVcMqYRtbMwrcXAGDbnscusyJuoGw3ZC92bIZAKfHvaWdPrq28rKIJoWvF84mhZAvPOX2RQXp2rYg2eIimvh3jGneRPQZDZD')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '831001625976586')
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

def _increment_counter_transaction(transaction, counter_ref):
    """Increments or creates counter. Returns new integer counter value."""
    snapshot = counter_ref.get(transaction=transaction)
    if snapshot.exists:
        current = snapshot.get('count') or 0
        new = int(current) + 1
        transaction.update(counter_ref, {'count': new})
    else:
        new = 1
        transaction.set(counter_ref, {'count': new})
    return new

# ============================================================
# WhatsApp Functions
# ============================================================

def send_text(to_number, message):
    """Send WhatsApp text message"""
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
    
    response = requests.post(url, json=payload, headers=headers)
    print(f"📤 Sent to {clean_number}: {response.status_code}")
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
# Flask Routes - General
# ============================================================

@app.route('/')
def home():
    return "✅ ZestRewards API is running!"

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
        print(f"✓ Bill: {bill_amount}")
        print(f"✓ Points: {points_earned}")
        
        if not all([transaction_id, customer_phone, bill_amount, points_earned]):
            print("❌ Missing required fields")
            return jsonify({"status": "error", "error": "Missing required fields"}), 400
        
        # Save simplified transaction
        print(f"💾 Saving transaction to Firebase...")
        db.collection('transactions').document(transaction_id).set({
            'transaction_id': transaction_id,
            'customer_phone': customer_phone,
            'restaurant_id': restaurant_id,
            'bill_amount': float(bill_amount),
            'points_earned': int(points_earned),
            'claimed_at': datetime.now()
        })
        print(f"✅ Transaction saved: {transaction_id}")
        
        # Update or create customer with simplified schema
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
                'last_visit': datetime.now()
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
                'registered_at': datetime.now(),
                'last_visit': datetime.now()
            })
            print(f"  ✅ New customer created: {customer_name or customer_phone}")
        
        print("="*60)
        print("✅ SUCCESS: Transaction completed and points added")
        print("="*60 + "\n")
        
        return jsonify({
            "status": "ok",
            "message": "Transaction created successfully",
            "transaction_id": transaction_id
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

@app.route('/check-balance', methods=['GET'])
def check_balance():
    """Query params: phone (required) - phone number"""
    phone = request.args.get('phone')
    if not phone:
        return jsonify({"status": "error", "error": "Missing phone parameter"}), 400

    phone_clean = clean_phone_number(phone)
    customer_id = f"{phone_clean}_{RESTAURANT_ID}"

    try:
        cust_ref = db.collection('customers').document(customer_id)
        cust_snap = cust_ref.get()
        if not cust_snap.exists:
            return jsonify({"status": "ok", "found": False, "message": "Customer not found"}), 200

        cust = cust_snap.to_dict()
        if cust.get('registered_at'):
            cust['registered_at'] = cust['registered_at'].isoformat()
        if cust.get('last_visit'):
            cust['last_visit'] = cust['last_visit'].isoformat()

        return jsonify({"status": "ok", "found": True, "customer": cust}), 200

    except Exception as e:
        print("ERROR check_balance:", e)
        return jsonify({"status": "error", "error": str(e)}), 500

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
        cust_snap = customer_ref.get(transaction=transaction)
        if not cust_snap.exists:
            raise ValueError("Customer not found")

        cust = cust_snap.to_dict()
        current_balance = int(cust.get('points_balance', 0))

        if points_to_redeem > current_balance:
            raise ValueError("Insufficient points")

        new_count = _increment_counter_transaction(transaction, counter_ref)
        redemption_id = f"R{new_count:04d}"

        now = datetime.now()
        
        # Simplified redemption document
        redemption_doc = {
            "redemption_id": redemption_id,
            "customer_phone": customer_phone,
            "points_redeemed": int(points_to_redeem),
            "reward_description": reward_description,
            "restaurant_id": restaurant_id,
            "created_at": now
        }

        redemption_ref = db.collection('redemptions').document(redemption_id)
        transaction.set(redemption_ref, redemption_doc)

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
    Send campaign messages to customer segments
    
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
                
                # Send WhatsApp message
                result = send_text(customer['phone_number'], personalized_msg)
                
                if result:
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

# ============================================================
# Flask Routes - WhatsApp Webhook
# ============================================================

@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Meta webhook verification"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    
    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("✅ Webhook verified!")
        return challenge, 200
    
    print("❌ Verification failed!")
    return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def receive_message():
    """Receive messages from Meta WhatsApp"""
    data = request.get_json()
    
    print("=" * 60)
    print("📨 Webhook received")
    print("=" * 60)
    
    try:
        value = data['entry'][0]['changes'][0]['value']
        
        if 'messages' in value:
            message = value['messages'][0]
            from_number = clean_phone_number(message['from'])
            
            if 'text' in message:
                text = message['text']['body']
                print(f"📱 From: {from_number}")
                print(f"💬 Message: {text}")
                
                if text.upper() == "BALANCE":
                    print(f"💰 Balance check for {from_number}")
                    
                    customer = get_customer(from_number, RESTAURANT_ID)
                    
                    if customer:
                        registered = customer.get('registered_at')
                        member_since = registered.strftime('%d %b %Y') if registered else 'N/A'
                        
                        message_text = f"""💰 ZestRewards Balance

Account Details:
━━━━━━━━━━━━━━━━━━━━
💎 Available Points: {customer.get('points_balance', 0)} points
📈 Total Earned: {customer.get('total_points_earned', 0)} points
🏆 Total Visits: {customer.get('total_visits', 0)}
📅 Member Since: {member_since}

Visit us again to earn more! 🎉"""
                    else:
                        message_text = """You don't have an account yet! 😊

Visit our restaurant and provide your phone number at checkout to start earning points! 🎁"""
                    
                    send_text(from_number, message_text)
                
                else:
                    print(f"❓ Unknown command: {text}")
                    
                    message_text = """Welcome to ZestRewards! 👋

Commands:
💰 BALANCE - Check your points

💡 How to earn points:
Visit our restaurant and provide your phone number at checkout!

Questions? Contact restaurant staff."""
                    
                    send_text(from_number, message_text)
        
        elif 'statuses' in value:
            status = value['statuses'][0]
            print(f"📊 Status: {status.get('status')}")
        
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================
# Run App
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 ZestRewards Backend Starting...")
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
