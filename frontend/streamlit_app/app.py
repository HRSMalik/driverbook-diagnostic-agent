import streamlit as st
import requests
import json
import time
import re

st.set_page_config(page_title="DriverBook Diagnostics Chatbot", layout="centered")

st.title("🚗 DriverBook Diagnostics Chatbot")
st.markdown("Chat with AI to diagnose your vehicle faults")

# API configuration
API_URL = "http://localhost:8000"

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.current_payload = {
        "vehicleId": "TRUCK-001",
        "dtcJson": {"dtcs": {}, "mil": False},
        "telemetry": {
            "engineCoolantTemperature": 90,
            "engineOilPressure": 40,
            "speed": 0,
            "fuelLevel": 70,
            "defLevel": 50,
            "engineSpeed": 0
        }
    }

# Display chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])

# Chat input
user_input = st.chat_input("Describe your vehicle fault...")

if user_input:
    # Add user message
    st.session_state.messages.append({"role": "user", "content": user_input})
    
    with st.chat_message("user"):
        st.write(user_input)
    
    # Simple payload for testing
    payload = {
        "vehicleId": "TRUCK-001",
        "dtcJson": {
            "dtcs": {
                "SPN 521133": {
                    "ecu": "Engine",
                    "desc": user_input
                }
            },
            "mil": True
        },
        "telemetry": {
            "engineCoolantTemperature": 95,
            "engineOilPressure": 40,
            "speed": 0,
            "fuelLevel": 70,
            "defLevel": 50
        }
    }
    
    # Send to API
    with st.chat_message("assistant"):
        with st.spinner("Analyzing..."):
            try:
                start_time = time.time()
                response = requests.post(
                    f"{API_URL}/analyze-fault",
                    json=payload,
                    timeout=60
                )
                elapsed_time = time.time() - start_time
                
                if response.status_code == 200:
                    result = response.json()
                    
                    response_text = f"**Diagnosis (completed in {elapsed_time:.2f}s)**\n\n"
                    
                    if "diagnostics" in result and result["diagnostics"]:
                        for diag in result["diagnostics"]:
                            response_text += f"**Code:** {diag.get('code', 'Unknown')}\n"
                            response_text += f"**Severity:** {diag.get('severity', 'N/A')} | "
                            response_text += f"**Urgency:** {diag.get('urgency', 'N/A')}\n\n"
                            response_text += f"{diag.get('issue', 'N/A')}\n\n"
                            response_text += f"**Explanation:** {diag.get('explanation', 'N/A')}\n\n"
                            response_text += "**Steps to fix:**\n"
                            for step in diag.get('resolution_steps', []):
                                response_text += f"• {step}\n"
                    
                    st.write(response_text)
                    st.session_state.messages.append({"role": "assistant", "content": response_text})
                else:
                    error_msg = f"Error: {response.status_code}"
                    st.error(error_msg)
                    st.session_state.messages.append({"role": "assistant", "content": error_msg})
            
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({"role": "assistant", "content": error_msg})

