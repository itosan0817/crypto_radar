
import os
import requests
from dotenv import load_dotenv

load_dotenv()

def test_webhook(name, url):
    print(f"Testing {name}...")
    try:
        response = requests.post(url, json={"content": f"🛠️ Status Check: Testing {name} notification. If you see this, the webhook is working locally."})
        if response.status_code == 204:
            print(f"Successfully sent message to {name}.")
        else:
            print(f"Failed to send message to {name}. Status: {response.status_code}, Response: {response.text}")
    except Exception as e:
        print(f"Error testing {name}: {e}")

radar_url = os.getenv("RADAR_WEBHOOK_URL")
bribe_url = os.getenv("BRIBE_WEBHOOK_URL")

test_webhook("Radar Webhook", radar_url)
test_webhook("Bribe Webhook", bribe_url)
