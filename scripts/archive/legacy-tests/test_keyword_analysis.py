#!/usr/bin/env python3
"""
Test the emergency recovery keyword analysis
"""
import sys
import os

# Add the module path
sys.path.append(os.path.join(os.path.dirname(__file__), 'modules', 'emergency-recovery', 'service'))

from emergency_recovery import EmergencyRecoveryModule

def test_analyze_input():
    """Test the analyze_user_input method"""
    
    module = EmergencyRecoveryModule()
    
    test_inputs = [
        "fix lmstudio",
        "lmstudio",
        "fix network",
        "network issues",
        "namespace reset"
    ]
    
    print("Testing keyword analysis:")
    for test_input in test_inputs:
        result = module.analyze_user_input(test_input)
        print(f"Input: '{test_input}' -> Action: '{result}'")

if __name__ == "__main__":
    test_analyze_input()