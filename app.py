from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
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
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '788247724379268')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'zest_rewards_webhook_2025')
RESTAURANT_ID = os.environ.get('RESTAURANT_ID', 'rest_001')

# ============================================================
# Initialize Firebase
# ============================================================

try:
    # Check if running on cloud (has environment variable)
    firebase_creds_base64 = os.environ.get('FIREBASE_CREDENTIALS_BASE64')
    
    if firebase_creds_base64:
        # Deployment: Decode base64 credentials
        print("🔐 Using Firebase credentials from environment variable")
        cred_json = base64.b64decode(firebase_creds_base64)
        cred_dict = json.loads(cred_json)
        cred = credentials.Certificate(cred_dict)
    else:
        # Local development: Use file
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
    # Remove +, spaces, dashes
    cleaned = phone.replace('+', '').replace(' ', '').replace('-', '')
    return cleaned

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

def update_optin_status(phone_number, restaurant_id, opted_in):
    """Update customer opt-in status"""
    if not db:
        return
    
    phone = clean_phone_number(phone_number)
    customer_id = f"{phone}_{restaurant_id}"
    customer_ref = db.collection('customers').document(customer_id)
    
    if customer_ref.get().exists:
        customer_ref.update({"opted_in": opted_in})
        print(f"✅ Updated {customer_id}: opted_in = {opted_in}")

# ============================================================
# Flask Routes - General
# ============================================================

@app.route('/')
def home():
    return "✅ ZestRewards API is running!"

# ============================================================
# Flask Routes - Frontend API
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
        customer_name = data.get('customer_name', '').strip()
        restaurant_id = data.get('restaurant_id', RESTAURANT_ID)
        bill_amount = data.get('bill_amount')
        points_earned = data.get('points_earned')
        
        print(f"✓ Transaction ID: {transaction_id}")
        print(f"✓ Phone (cleaned): {customer_phone}")
        print(f"✓ Customer Name: {customer_name or 'Not provided'}")
        print(f"✓ Bill: {bill_amount}")
        print(f"✓ Points: {points_earned}")
        
        # Validate required fields
        if not all([transaction_id, customer_phone, bill_amount, points_earned]):
            print("❌ Missing required fields")
            return jsonify({"status": "error", "error": "Missing required fields"}), 400
        
        # Save to transactions collection with status "completed"
        print(f"💾 Saving transaction to Firebase...")
        db.collection('transactions').document(transaction_id).set({
            'transaction_id': transaction_id,
            'customer_phone': customer_phone,
            'restaurant_id': restaurant_id,
            'bill_amount': float(bill_amount),
            'points_earned': int(points_earned),
            'status': 'completed',
            'created_at': datetime.now(),
            'claimed_at': datetime.now(),
            'added_by': data.get('added_by', 'frontend'),
            'notes': data.get('notes', '')
        })
        print(f"✅ Transaction saved as COMPLETED: {transaction_id}")
        
        # Update or create customer (adds points immediately)
        print(f"👤 Updating customer profile...")
        customer_id = f"{customer_phone}_{restaurant_id}"
        customer_ref = db.collection('customers').document(customer_id)
        customer_snap = customer_ref.get()
        
        if customer_snap.exists:
            print(f"  → Customer exists, adding points...")
            current = customer_snap.to_dict()
            
            # Update points and visits
            update_data = {
                'points_balance': current.get('points_balance', 0) + int(points_earned),
                'total_points_earned': current.get('total_points_earned', 0) + int(points_earned),
                'total_visits': current.get('total_visits', 0) + 1,
                'last_visit': datetime.now()
            }
            
            # Update name if provided and not already set
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
                'restaurant_name': 'Zest Restaurant',
                'points_balance': int(points_earned),
                'total_points_earned': int(points_earned),
                'total_visits': 1,
                'opted_in': False,
                'status': 'active',
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
                
                # ============================================
                # Handle BALANCE - Check Points
                # ============================================
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
                
                # ============================================
                # Handle YES - Opt In
                # ============================================
                elif text.upper() == "YES":
                    print("✅ Processing opt-in...")
                    
                    customer = get_customer(from_number, RESTAURANT_ID)
                    
                    if customer:
                        update_optin_status(from_number, RESTAURANT_ID, True)
                        
                        message_text = """Perfect! 🎉

You're now subscribed to exclusive offers from Zest Restaurant.

You'll receive:
✨ Special deals & promotions
🎂 Birthday surprises
🎁 Exclusive early access

Reply NO anytime to unsubscribe.

Thank you! 💎"""
                    else:
                        message_text = """Please visit our restaurant first! 😊

Provide your phone number at checkout to create your account."""
                    
                    send_text(from_number, message_text)
                
                # ============================================
                # Handle NO - Opt Out
                # ============================================
                elif text.upper() == "NO":
                    print("❌ Processing opt-out...")
                    
                    customer = get_customer(from_number, RESTAURANT_ID)
                    
                    if customer:
                        update_optin_status(from_number, RESTAURANT_ID, False)
                        message_text = """No problem! 😊

You can still collect and redeem points with every visit.

Reply YES anytime to get exclusive offers.

Thank you! 🙏"""
                    else:
                        message_text = """No problem! 😊

You can still earn points by visiting our restaurant and providing your phone number at checkout.

Thank you! 🙏"""
                    
                    send_text(from_number, message_text)
                
                # ============================================
                # Unknown Command - Help
                # ============================================
                else:
                    print(f"❓ Unknown command: {text}")
                    
                    message_text = """Welcome to ZestRewards! 👋

Commands:
💰 BALANCE - Check your points
🎁 YES - Subscribe to exclusive offers
🚫 NO - Decline offers

💡 How to earn points:
Visit our restaurant and provide your phone number at checkout!

Questions? Contact restaurant staff."""
                    
                    send_text(from_number, message_text)
        
        # Log status updates
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
    
    # Use environment variable PORT if available (for deployment)
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)
