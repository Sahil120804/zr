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


def send_text_dynamic(restaurant_id, to_number, message):
    """
    Send WhatsApp text message using restaurant-specific credentials
    Fetches phone_number_id and access_token from Firebase
    """
    try:
        # Fetch restaurant credentials from Firebase
        restaurant_ref = db.collection('restaurants').document(restaurant_id)
        restaurant_snap = restaurant_ref.get()
        
        if not restaurant_snap.exists:
            print(f"❌ Restaurant {restaurant_id} not found in Firebase")
            # Fallback to environment variables
            phone_number_id = PHONE_NUMBER_ID
            access_token = WHATSAPP_TOKEN
            print(f"⚠️ Using fallback credentials from environment")
        else:
            restaurant_data = restaurant_snap.to_dict()
            phone_number_id = restaurant_data.get('phone_number_id', PHONE_NUMBER_ID)
            access_token = restaurant_data.get('access_token', WHATSAPP_TOKEN)
            
            if not phone_number_id or not access_token:
                print(f"❌ Missing WhatsApp credentials for restaurant {restaurant_id}")
                # Fallback to environment
                phone_number_id = PHONE_NUMBER_ID
                access_token = WHATSAPP_TOKEN
        
        clean_number = clean_phone_number(to_number)
        
        url = f"https://graph.facebook.com/v21.0/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_number,
            "type": "text",
            "text": {"body": message}
        }
        
        response = requests.post(url, json=payload, headers=headers)
        print(f"📤 Sent to {clean_number} via {restaurant_id}: {response.status_code}")
        
        if response.status_code != 200:
            print(f"❌ WhatsApp API Error: {response.text}")
        
        return response.json()
        
    except Exception as e:
        print(f"❌ Error in send_text_dynamic: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def send_text(to_number, message):
    """Legacy function for backward compatibility - uses environment variables"""
    return send_text_dynamic(RESTAURANT_ID, to_number, message)


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


@app.route('/check-balance', methods=['GET'])
def check_balance():
    """Query params: phone (required), restaurant_id (optional), force_recalc (optional)"""
    phone = request.args.get('phone')
    if not phone:
        return jsonify({"status": "error", "error": "Missing phone parameter"}), 400

    phone_clean = clean_phone_number(phone)
    restaurant_id = request.args.get('restaurant_id', RESTAURANT_ID)
    force_recalc = request.args.get('force_recalc', 'false').lower() == 'true'
    customer_id = f"{phone_clean}_{restaurant_id}"

    print(f"💰 Balance check - Phone: {phone_clean}, Restaurant: {restaurant_id}, force_recalc={force_recalc}")

    try:
        cust_ref = db.collection('customers').document(customer_id)
        cust_snap = cust_ref.get()
        if not cust_snap.exists:
            return jsonify({"status": "ok", "found": False, "message": "Customer not found"}), 200

        cust = cust_snap.to_dict()

        def _iso(ts):
            try:
                return ts.astimezone(timezone.utc).isoformat()
            except Exception:
                return None

        result = {
            "status": "ok",
            "found": True,
            "customer": {
                "customer_name": cust.get('customer_name'),
                "phone_number": cust.get('phone_number'),
                "points_balance": int(cust.get('points_balance', 0)),
                "total_points_earned": int(cust.get('total_points_earned', 0)),
                "total_points_redeemed": int(cust.get('total_points_redeemed', 0)),
                "total_visits": int(cust.get('total_visits', 0)),
                "registered_at": _iso(cust.get('registered_at')) if cust.get('registered_at') else None,
                "last_visit": _iso(cust.get('last_visit')) if cust.get('last_visit') else None,
                "last_redeemed_at": _iso(cust.get('last_redeemed_at')) if cust.get('last_redeemed_at') else None,
            }
        }

        if force_recalc:
            total_remaining = 0
            pe_q = db.collection('point_events')\
                     .where('customer_phone', '==', phone_clean)\
                     .where('restaurant_id', '==', restaurant_id)\
                     .where('status', 'in', ['active','partial'])\
                     .stream()

            for pe in pe_q:
                pe_data = pe.to_dict()
                total_remaining += int(pe_data.get('remaining', pe_data.get('points', 0) or 0))

            result['customer']['recomputed_points_balance'] = total_remaining
            result['customer']['balance_match'] = (int(result['customer']['points_balance']) == total_remaining)

        return jsonify(result), 200

    except Exception as e:
        print("ERROR check_balance:", e)
        import traceback
        traceback.print_exc()
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

        pe_query = db.collection('point_events')\
                     .where('customer_phone', '==', customer_phone)\
                     .where('restaurant_id', '==', restaurant_id)\
                     .where('status', 'in', ['active', 'partial'])\
                     .order_by('expires_at')\
                     .limit(200)

        remaining_to_consume = points_to_redeem
        consumed_events = []

        for pe_doc in pe_query.stream():
            if remaining_to_consume <= 0:
                break

            pe = pe_doc.to_dict()
            pe_id = pe_doc.id
            avail = int(pe.get('remaining', pe.get('points', 0) or 0))
            if avail <= 0:
                continue

            use = min(avail, remaining_to_consume)
            new_remaining = avail - use
            pe_ref = db.collection('point_events').document(pe_id)

            if new_remaining == 0:
                transaction.update(pe_ref, {
                    'remaining': 0,
                    'status': 'redeemed',
                    'redeemed_at': datetime.now(timezone.utc)
                })
            else:
                transaction.update(pe_ref, {
                    'remaining': new_remaining,
                    'status': 'partial',
                    'redeemed_at': datetime.now(timezone.utc)
                })

            consumed_events.append({"point_event_id": pe_id, "used": use})
            remaining_to_consume -= use

        if remaining_to_consume > 0:
            raise ValueError("Not enough usable points in events")

        new_count = _increment_counter_transaction(transaction, counter_ref)
        redemption_id = f"R{new_count:04d}"

        now = datetime.now(timezone.utc)
        
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
                
                # Send WhatsApp message - USE DYNAMIC VERSION
                result = send_text_dynamic(restaurant_id, customer['phone_number'], personalized_msg)
                
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


# ============================================================
# Flask Routes - Meta Embedded Signup
# ============================================================


@app.route('/meta-callback', methods=['GET'])
def meta_callback():
    """
    Meta Embedded Signup callback
    Receives authorization code, exchanges for token, stores in Firebase
    
    Query params:
      - code: Authorization code from Meta
      - state: Optional restaurant_id
    """
    print("\n" + "="*60)
    print("🔗 META EMBEDDED SIGNUP CALLBACK")
    print("="*60)
    
    try:
        code = request.args.get('code')
        state = request.args.get('state')  # Optional restaurant_id
        
        if not code:
            return jsonify({"error": "Missing authorization code"}), 400
        
        print(f"📥 Received code: {code[:20]}...")
        print(f"📥 State (restaurant_id): {state}")
        
        # Step 1: Exchange code for access token
        token_url = "https://graph.facebook.com/v21.0/oauth/access_token"
        token_params = {
            "client_id": os.environ.get('META_APP_ID'),
            "client_secret": os.environ.get('META_APP_SECRET'),
            "code": code
        }
        
        print("🔄 Exchanging code for access token...")
        token_response = requests.get(token_url, params=token_params)
        token_data = token_response.json()
        
        if 'error' in token_data:
            print(f"❌ Token exchange failed: {token_data}")
            return jsonify({"error": "Failed to exchange code", "details": token_data}), 400
        
        access_token = token_data.get('access_token')
        print(f"✅ Access token received: {access_token[:20]}...")
        
        # Step 2: Get token debug info to find WABA ID
        debug_url = f"https://graph.facebook.com/v21.0/debug_token?input_token={access_token}&access_token={access_token}"
        debug_response = requests.get(debug_url)
        debug_data = debug_response.json()
        
        granular_scopes = debug_data.get('data', {}).get('granular_scopes', [])
        print(f"📋 Granted scopes: {granular_scopes}")
        
        # Extract WABA ID
        waba_id = None
        for scope in granular_scopes:
            if scope.get('scope') == 'whatsapp_business_messaging':
                waba_id = scope.get('target_ids', [None])[0]
                break
        
        if not waba_id:
            return jsonify({"error": "Could not determine WABA ID from token"}), 400
        
        print(f"✅ WABA ID: {waba_id}")
        
        # Step 3: Get phone numbers for this WABA
        phone_url = f"https://graph.facebook.com/v21.0/{waba_id}/phone_numbers?access_token={access_token}"
        phone_response = requests.get(phone_url)
        phone_data = phone_response.json()
        
        if 'error' in phone_data or not phone_data.get('data'):
            return jsonify({"error": "No phone numbers found for WABA", "details": phone_data}), 400
        
        # Get first phone number
        phone_info = phone_data['data'][0]
        phone_number_id = phone_info['id']
        display_phone = phone_info.get('display_phone_number')
        verified_name = phone_info.get('verified_name')
        
        print(f"✅ Phone Number ID: {phone_number_id}")
        print(f"✅ Display Number: {display_phone}")
        print(f"✅ Verified Name: {verified_name}")
        
        # Step 4: Get Business Manager ID
        business_id = debug_data.get('data', {}).get('app_id')
        
        # Generate restaurant_id if not provided
        if not state:
            state = f"rest_{display_phone[-6:]}" if display_phone else f"rest_{phone_number_id[-6:]}"
        
        restaurant_id = state
        
        # Step 5: Store in Firebase with your schema
        now = datetime.now(timezone.utc)
        restaurant_data = {
            "restaurant_id": restaurant_id,
            "restaurant_name": verified_name or "New Restaurant",
            "points_expiry_days": 90,
            "created_at": now,
            
            "business_id": business_id,
            "business_name": verified_name,
            "waba_id": waba_id,
            "phone_number_id": phone_number_id,
            "display_phone_number": display_phone,
            "access_token": access_token,
            "connected_at": now
        }
        
        db.collection('restaurants').document(restaurant_id).set(restaurant_data, merge=True)
        print(f"✅ Restaurant {restaurant_id} saved to Firebase")
        
        print("="*60)
        print("✅ META SIGNUP COMPLETE")
        print("="*60 + "\n")
        
        # Return success HTML page
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>WhatsApp Connected!</title>
            <style>
                body {{
                    font-family: 'Segoe UI', sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                }}
                .container {{
                    background: white;
                    padding: 50px;
                    border-radius: 16px;
                    box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                    text-align: center;
                    max-width: 600px;
                }}
                h1 {{ color: #667eea; margin-bottom: 20px; font-size: 32px; }}
                .checkmark {{ color: #4caf50; font-size: 80px; margin-bottom: 20px; }}
                .info {{
                    background: #f5f7ff;
                    padding: 20px;
                    border-radius: 12px;
                    margin: 30px 0;
                    text-align: left;
                    border-left: 4px solid #667eea;
                }}
                .info p {{ margin: 10px 0; font-size: 15px; }}
                .info strong {{ color: #667eea; }}
                .btn {{
                    display: inline-block;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 14px 32px;
                    border-radius: 8px;
                    text-decoration: none;
                    font-weight: 600;
                    margin-top: 20px;
                    transition: transform 0.2s;
                }}
                .btn:hover {{ transform: translateY(-2px); }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="checkmark">✅</div>
                <h1>WhatsApp Successfully Connected!</h1>
                <p>Your restaurant is now integrated with ZestRewards</p>
                <div class="info">
                    <p><strong>Restaurant ID:</strong> {restaurant_id}</p>
                    <p><strong>Phone Number:</strong> {display_phone}</p>
                    <p><strong>Business Name:</strong> {verified_name}</p>
                    <p><strong>WABA ID:</strong> {waba_id}</p>
                </div>
                <a href="https://YOUR-GITHUB-USERNAME.github.io/zestrewards-frontend/?rest_id={restaurant_id}" class="btn">
                    Open Your Dashboard →
                </a>
                <p style="margin-top: 30px; font-size: 13px; color: #666;">
                    Save your Restaurant ID: <code style="background:#f0f0f0;padding:4px 8px;border-radius:4px;">{restaurant_id}</code>
                </p>
            </div>
        </body>
        </html>
        """, 200
        
    except Exception as e:
        print(f"❌ ERROR in meta_callback: {e}")
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
        
        # Determine which restaurant this message belongs to
        metadata = value.get('metadata', {})
        phone_number_id = metadata.get('phone_number_id')
        
        # Find restaurant by phone_number_id
        restaurant_id = RESTAURANT_ID  # Default fallback
        if phone_number_id:
            print(f"🔍 Looking for restaurant with phone_number_id: {phone_number_id}")
            restaurants_query = db.collection('restaurants')\
                .where('phone_number_id', '==', phone_number_id)\
                .limit(1)\
                .stream()
            
            for rest_doc in restaurants_query:
                restaurant_id = rest_doc.id
                print(f"✅ Matched message to restaurant: {restaurant_id}")
                break
        
        print(f"📍 Using restaurant_id: {restaurant_id}")
        
        if 'messages' in value:
            message = value['messages'][0]
            from_number = clean_phone_number(message['from'])
            
            if 'text' in message:
                text = message['text']['body']
                print(f"📱 From: {from_number}")
                print(f"💬 Message: {text}")
                
                if text.upper() == "BALANCE":
                    print(f"💰 Balance check for {from_number}")
                    
                    customer = get_customer(from_number, restaurant_id)
                    
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
                    
                    send_text_dynamic(restaurant_id, from_number, message_text)
                
                else:
                    print(f"❓ Unknown command: {text}")
                    
                    message_text = """Welcome to ZestRewards! 👋


Commands:
💰 BALANCE - Check your points


💡 How to earn points:
Visit our restaurant and provide your phone number at checkout!


Questions? Contact restaurant staff."""
                    
                    send_text_dynamic(restaurant_id, from_number, message_text)
        
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
