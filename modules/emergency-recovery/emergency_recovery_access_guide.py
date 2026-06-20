#!/usr/bin/env python3
"""
Emergency Recovery Module Access Guide

This guide shows you the different ways to access the Ollama restart function
through the AI Stack pipe system.
"""

import json

# Ways to access the Ollama restart function:

def show_access_methods():
    print("🔧 How to Access the Ollama Restart Function")
    print("=" * 50)
    
    print("\n1️⃣ Through OpenWebUI Chat Interface (Recommended)")
    print("   In OpenWebUI chat, simply type any of these phrases:")
    print("   • 'restart ollama'")
    print("   • 'fix ollama connectivity'") 
    print("   • 'ollama restart'")
    print("   • 'repair ollama connection'")
    print("   • 'emergency restart ollama'")
    
    print("\n2️⃣ Direct Module Test (Command Line)")
    print("   From the project root directory:")
    print('   echo \'{"request_id": "test", "input": "restart ollama"}\' | python modules\\emergency-recovery\\service\\emergency_recovery.py')
    
    print("\n3️⃣ Through Unified Pipe Function (OpenWebUI Functions)")
    print("   If you have the unified_openwebui_pipe.py installed as an OpenWebUI function:")
    print("   Just chat with phrases like 'emergency fix ollama' or 'recovery restart ollama'")
    
    print("\n4️⃣ Direct Router Call (Development/Testing)")
    print("   python core\\router.py --input \"restart ollama\"")
    
    print("\n📝 Expected Response Format:")
    print("   The function will return JSON with:")
    print("   - action: 'restart_ollama'")
    print("   - status: 'completed' (on success) or 'error' (on failure)")
    print("   - steps_completed: List of steps performed")
    print("   - connectivity_test: Results of testing Ollama connectivity")
    print("   - next_steps: Recommended actions after restart")

def test_keyword_detection():
    """Test what keywords trigger the emergency recovery module"""
    print("\n🎯 Keyword Detection Test")
    print("=" * 30)
    
    # Import the router to test keyword detection
    import sys, os
    sys.path.append(os.path.join(os.path.dirname(__file__), 'core'))
    
    try:
        from router import AIStackRouter
        router = AIStackRouter()
        
        test_phrases = [
            "restart ollama",
            "fix ollama connectivity", 
            "ollama emergency restart",
            "repair ollama connection",
            "emergency recovery ollama",
            "recovery restart service"
        ]
        
        print("Testing which phrases route to emergency-recovery:")
        for phrase in test_phrases:
            detected_module = router._analyze_input_for_routing(phrase)
            status = "✅ CORRECT" if detected_module == "emergency-recovery" else "❌ WRONG"
            print(f"   '{phrase}' → {detected_module} {status}")
            
    except ImportError as e:
        print(f"   Could not import router for testing: {e}")

def show_setup_instructions():
    print("\n⚙️ Setup Instructions for OpenWebUI")
    print("=" * 40)
    
    print("\n🔧 Option A: Using Unified Pipe Function (Recommended)")
    print("   1. Go to OpenWebUI → Admin → Functions")
    print("   2. Create new function or edit existing 'AI Stack Unified'")
    print("   3. Copy contents of scripts\\ai_pipes\\unified_openwebui_pipe.py")
    print("   4. Save the function")
    print("   5. Chat with phrases like 'restart ollama' or 'emergency fix'")
    
    print("\n🔧 Option B: Direct Testing (Development)")
    print("   1. Open terminal in the ai-stack directory")
    print("   2. Run the test command:")
    print("   3. cd \"d:\\Open WebUI\\ai-stack\"")  
    print('   4. echo \'{"request_id": "user-test", "input": "restart ollama"}\' | python modules\\emergency-recovery\\service\\emergency_recovery.py')
    
    print("\n📋 Prerequisites:")
    print("   • Docker and Docker Compose must be running")
    print("   • OpenWebUI container should be running")
    print("   • Run from the ai-stack project root directory")

def show_example_responses():
    print("\n📄 Example Response - Successful Restart")
    print("=" * 40)
    
    success_example = {
        "request_id": "user-test",
        "module_id": "emergency-recovery",
        "status": "ok",
        "structured_data": {
            "action": "restart_ollama",
            "status": "completed",
            "steps_completed": [
                "Docker Compose availability verified",
                "Ollama service stopped", 
                "Clean shutdown wait completed",
                "Ollama service restarted",
                "Service readiness wait completed"
            ],
            "connectivity_test": {
                "status": "success",
                "message": "Ollama v0.12.1 is responding",
                "endpoint": "http://localhost:11434/api/version"
            },
            "next_steps": [
                "Test model loading in OpenWebUI",
                "Verify chat functionality",
                "Check for any remaining network issues"
            ]
        }
    }
    
    print(json.dumps(success_example, indent=2))

if __name__ == "__main__":
    show_access_methods()
    test_keyword_detection()
    show_setup_instructions() 
    show_example_responses()