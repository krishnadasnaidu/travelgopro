from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import boto3
from boto3.dynamodb.conditions import Key, Attr
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

from decimal import Decimal
from functools import wraps
import uuid # Corrected: Added back the import for uuid

app = Flask(__name__)

# --- Configuration (Hardcoded for this request - NOT RECOMMENDED FOR PRODUCTION) ---
# Flask Secret Key for session management.
app.secret_key = '341d79c4daf55aeea161e9eb39916041c20bd5cdb96efbf5ed177243cccdab4a'

# AWS Region for DynamoDB and SNS
REGION = 'us-east-1'

# AWS DynamoDB Resource Initialization
# Boto3 will automatically look for credentials via AWS CLI config, environment variables, etc.
try:
    dynamodb = boto3.resource('dynamodb', region_name=REGION)
    users_table = dynamodb.Table('travelgo_users')
    bookings_table = dynamodb.Table('bookings')
except Exception as e:
    # In a production app, you'd log this error properly.
    # For this example, we set to None and handle gracefully in routes.
    users_table = None
    bookings_table = None

# AWS SNS Client Initialization
try:
    sns_client = boto3.client('sns', region_name=REGION)
    # REPLACE WITH YOUR ACTUAL AWS ACCOUNT ID AND SNS TOPIC NAME
    SNS_TOPIC_ARN = 'arn:aws:sns:us-east-1:YOUR_AWS_ACCOUNT_ID:YOUR_SNS_TOPIC_NAME'
except Exception as e:
    sns_client = None
    SNS_TOPIC_ARN = None

# Email (SMTP) Setup
SMTP_SERVER = 'smtp.gmail.com' # e.g., 'smtp.gmail.com' for Gmail
SMTP_PORT = 587 # 587 for TLS, 465 for SSL
SENDER_EMAIL = 'your_email@example.com' # REPLACE WITH YOUR SENDER EMAIL
SENDER_PASSWORD = 'your_email_app_password' # REPLACE WITH YOUR EMAIL APP PASSWORD (e.g., Gmail App Password)

# --- Helper Functions for Notifications ---

def send_sns_notification(subject, message):
    if sns_client and SNS_TOPIC_ARN and "YOUR_AWS_ACCOUNT_ID" not in SNS_TOPIC_ARN:
        try:
            sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=subject,
                Message=message
            )
        except Exception as e:
            # Log the SNS error, but don't stop the application
            pass
    else:
        pass # SNS not configured or placeholder ARN

def send_email_notification(recipient_email, subject, body):
    # Skip sending if email credentials are placeholders
    if SENDER_EMAIL == 'your_email@example.com' or SENDER_PASSWORD == 'your_email_app_password':
        return
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = recipient_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls() # Enable TLS encryption
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        # Log the email error, but don't stop the application
        pass

# --- Decorator for Login Required ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_email' not in session:
            flash('Please login first', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- Routes ---

@app.route('/')
def home():
    # If a user is logged in, show the home page. Otherwise, direct to signin/registration.
    if 'user_email' in session:
        return render_template('home.html')
    else:
        return redirect(url_for('signin'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_email' in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            flash('Please fill in all fields', 'danger')
            return redirect(url_for('login'))
        
        if users_table is None:
            flash('Database service unavailable. Please try again later.', 'danger')
            return redirect(url_for('login'))

        try:
            response = users_table.get_item(Key={'email': email})
            user = response.get('Item')

            if user and check_password_hash(user['password'], password):
                session['user_email'] = user['email']
                # Increment login_count
                users_table.update_item(
                    Key={'email': email},
                    UpdateExpression="SET login_count = if_not_exists(login_count, :start) + :inc",
                    ExpressionAttributeValues={
                        ':inc': Decimal(1),
                        ':start': Decimal(0)
                    }
                )
                flash('Login successful!', 'success')
                return redirect(url_for('home'))
            
            flash('Invalid email or password', 'danger')
        except Exception as e:
            flash(f'An error occurred during login: {e}', 'danger')
    return render_template('login.html')

@app.route('/signin', methods=['GET', 'POST'])
def signin():
    if 'user_email' in session:
        return redirect(url_for('home'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        if not email or not password:
            flash('Please fill in all fields', 'danger')
            return redirect(url_for('signin'))
        
        if users_table is None:
            flash('Database service unavailable. Please try again later.', 'danger')
            return redirect(url_for('signin'))

        try:
            response = users_table.get_item(Key={'email': email})
            if response.get('Item'):
                flash('Email already registered', 'danger')
                return redirect(url_for('signin'))
            
            # Store initial login_count as 0
            users_table.put_item(
                Item={
                    'email': email,
                    'password': generate_password_hash(password).decode('utf-8'), # Decode for string storage
                    'login_count': Decimal(0) # Initialize login count for new user
                }
            )
            flash('Account created successfully! Please login.', 'success')

            # Send SNS notification for new registration
            send_sns_notification(
                subject="New User Registered",
                message=f"A new user with email {email} has successfully registered."
            )

            return redirect(url_for('login'))
        except Exception as e:
            flash(f'An error occurred during registration: {e}', 'danger')
            return redirect(url_for('signin'))
    
    return render_template('signin.html')

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    flash('You have been logged out', 'info')
    return redirect(url_for('home'))

@app.route('/flights', methods=['GET', 'POST'])
@login_required
def flights():
    available_flights = []
    if request.method == 'POST':
        from_location = request.form.get('from')
        to_location = request.form.get('to')
        travel_date = request.form.get('date')

        if from_location and to_location and travel_date:
            available_flights = [
                {'id': 1, 'from': from_location, 'to': to_location, 'date': travel_date, 'price': 100},
                {'id': 2, 'from': from_location, 'to': to_location, 'date': travel_date, 'price': 150}
            ]
        else:
            flash("Please provide all flight search details.", "warning")

    return render_template('flights.html', flights=available_flights)

@app.route('/hotels', methods=['GET', 'POST'])
@login_required
def hotels():
    available_hotels = []
    if request.method == 'POST':
        location = request.form.get('to')
        checkin_date = request.form.get('date')
        checkout_date = request.form.get('checkout')
        num_guests = request.form.get('guests')

        if location and checkin_date and checkout_date and num_guests:
            available_hotels = [
                {'id': 1, 'location': location, 'checkin': checkin_date, 'checkout': checkout_date, 'price': 200},
                {'id': 2, 'location': location, 'checkin': checkin_date, 'checkout': checkin_date, 'price': 250}
            ]
        else:
            flash("Please provide all hotel search details.", "warning")

    return render_template('hotels.html', hotels=available_hotels)

@app.route('/trains', methods=['GET', 'POST'])
@login_required
def trains():
    available_trains = []
    if request.method == 'POST':
        from_station = request.form.get('from')
        to_station = request.form.get('to')
        travel_date = request.form.get('date')

        if from_station and to_station and travel_date:
            available_trains = [
                {'id': 1, 'from': from_station, 'to': to_station, 'date': travel_date, 'price': 80},
                {'id': 2, 'from': from_station, 'to': to_station, 'date': travel_date, 'price': 90}
            ]
        else:
            flash("Please provide all train search details.", "warning")

    return render_template('trains.html', trains=available_trains)

@app.route('/buses', methods=['GET', 'POST'])
@login_required
def buses():
    available_buses = []
    if request.method == 'POST':
        from_location = request.form.get('from')
        to_location = request.form.get('to')
        travel_date = request.form.get('date')

        if from_location and to_location and travel_date:
            available_buses = [
                {'id': 1, 'from': from_location, 'to': to_location, 'date': travel_date, 'price': 50},
                {'id': 2, 'from': from_location, 'to': to_location, 'date': travel_date, 'price': 60}
            ]
        else:
            flash("Please provide all bus search details.", "warning")

    return render_template('buses.html', buses=available_buses)

@app.route('/book', methods=['GET', 'POST'])
@login_required
def book():
    user_email = session.get('user_email')

    if not user_email:
        flash('User not logged in. Please log in to book.', 'danger')
        return redirect(url_for('login'))
    
    if bookings_table is None:
        flash('Database service unavailable. Please try again later.', 'danger')
        return redirect(url_for('home'))

    if request.method == 'POST':
        booking_type = request.form.get('type')
        booking_details = {}
        redirect_page = 'home'

        if booking_type == 'event':
            tickets_str = request.form.get('tickets')
            num_tickets = 1
            if tickets_str:
                try:
                    num_tickets = int(tickets_str)
                except ValueError:
                    flash('Invalid number of tickets entered. Defaulting to 1.', 'warning')
            
            booking_details.update({
                'event_name': request.form.get('event_name', ''),
                'event_date': request.form.get('event_date', ''),
                'location': request.form.get('location', ''),
                'tickets': Decimal(num_tickets)
            })
            redirect_page = 'book'
        
        elif booking_type == 'hotel':
            num_guests_str = request.form.get('guests')
            num_guests = 1
            if num_guests_str:
                try:
                    num_guests = int(num_guests_str)
                except ValueError:
                    flash('Invalid number of guests entered. Defaulting to 1.', 'warning')
            
            booking_details.update({
                'location': request.form.get('to', ''),
                'checkin_date': request.form.get('date', ''),
                'checkout_date': request.form.get('checkout', ''),
                'num_guests': Decimal(num_guests)
            })
            redirect_page = 'hotels'
        
        elif booking_type in ['bus', 'flight', 'train']:
            booking_details.update({
                'from': request.form.get('from', ''),
                'to': request.form.get('to', ''),
                'date': request.form.get('date', '')
            })
            redirect_page = f'{booking_type}s'
        
        else:
            flash('Invalid booking type selected.', 'danger')
            return redirect(url_for('home'))

        booking_record = {
            'booking_id': str(uuid.uuid4()), # Unique ID for DynamoDB primary key
            'user_email': user_email, # Partition Key for 'bookings' table
            'booking_type': booking_type,
            'booking_date': datetime.now().isoformat(), # Sort Key for 'bookings' table, stored as ISO string
            'status': 'pending',
            'details': booking_details
        }
        
        try:
            bookings_table.put_item(Item=booking_record)
            flash('Booking request submitted successfully! Proceeding to confirmation.', 'success')

            # Email notification for user
            email_subject = f"Your {booking_type.capitalize()} Booking Request Confirmation (ID: {booking_record['booking_id']})"
            email_body = f"""Dear {user_email},

Thank you for your booking request for a {booking_type}.
Your Booking ID: {booking_record['booking_id']}
Details:
{
    '\\n'.join([f"    {key.replace('_', ' ').title()}: {value}" for key, value in booking_details.items()])
}
Booking Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Your request has been submitted and is currently pending confirmation.
We will notify you once your booking is confirmed.

Best regards,
TravelGo Team
"""
            send_email_notification(user_email, email_subject, email_body)

            # SNS notification for admin/system
            sns_subject = f"New {booking_type.capitalize()} Booking Request (ID: {booking_record['booking_id']})"
            sns_message = f"User {user_email} submitted a new {booking_type} booking request:\\nBooking ID: {booking_record['booking_id']}\\nDetails: {booking_details}"
            send_sns_notification(sns_subject, sns_message)

            return redirect(url_for('payment_success', booking_type=booking_type))
        except Exception as e:
            flash(f'An error occurred during booking: {e}', 'danger')
            return redirect(url_for(redirect_page))
    
    else: # Handles GET request to /book
        booking_type = request.args.get('type')
        if booking_type == 'event':
            return render_template('ticketbooking.html')
        elif booking_type == 'flight':
            return redirect(url_for('flights'))
        elif booking_type == 'hotel':
            return redirect(url_for('hotels'))
        elif booking_type == 'train':
            return redirect(url_for('trains'))
        elif booking_type == 'bus':
            return redirect(url_for('buses'))
        else:
            flash('Invalid or no booking type specified for GET request.', 'danger')
            return redirect(url_for('home'))

@app.route('/payment_success')
@login_required
def payment_success():
    booking_type = request.args.get('booking_type', 'booking')
    return render_template('payment_success.html', booking_type=booking_type)

if __name__ == '__main__':
    app.run(debug=True) # Run on default port 5000, 0.0.0.0 for external access if needed