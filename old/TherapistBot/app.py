from flask import Flask, render_template, request, jsonify
# Use the modern Google GenAI SDK imports
from google import genai
from google.genai import types
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

# Set up Flask app
# IMPORTANT: The 'templates' folder must exist in the same directory as this file.
app = Flask(__name__, template_folder='templates')

# --- Gemini Client Setup ---
# Ensure GEMINI_API_KEY is set in .env
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY not set. Set it in your .env file.")

try:
    # Initialize the modern Gemini Client using the API key
    client = genai.Client(api_key=GEMINI_API_KEY)
except Exception as e:
    print(f"Error initializing Gemini client: {e}")
    client = None

@app.route('/')
def home():
    """Renders the main chat interface."""
    return render_template('index.html')

@app.route('/predict', methods=['POST'])
def predict():
    """Handles the chat message, calls the Gemini API, and returns the response."""
    data = request.get_json(silent=True) or {}
    user_message = data.get('message', '').strip()
    
    if not client:
        return jsonify({'error': 'AI client is not initialized. Check your GEMINI_API_KEY.'}), 503
        
    if not user_message:
        return jsonify({'error': 'No message provided.'}), 400

    try:
        # Generate AI response using the modern generate_content method
        # Using gemini-2.5-flash for fast, high-quality chat responses.
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_message,
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=8224,
            )
        )

        # The response text is extracted simply and robustly using .text
        ai_text = response.text
        
        return jsonify({'reply': ai_text})
        
    except Exception as e:
        # Catch any errors during the API call or processing
        print(f"Gemini API Error: {e}")
        return jsonify({'error': f'An error occurred during API call: {str(e)}'}), 500

if __name__ == '__main__':
    # Ensure you are not running in debug=True in a production environment
    app.run(debug=True, port=5500)