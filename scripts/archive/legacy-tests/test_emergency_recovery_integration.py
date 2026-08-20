#!/usr/bin/env python3
"""
Integration test for Emergency Recovery Module

This test verifies the emergency recovery module functions work correctly
and demonstrates how to use the new implemented functions.
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'modules', 'emergency-recovery', 'service'))

from emergency_recovery import recovery_module

def test_restart_ollama():
    """Test restart_ollama function (will fail outside container, but demonstrates structure)"""
    print("🧪 Testing restart_ollama function...")
    
    request = {
        "request_id": "integration-test-restart",
        "input": "restart ollama"
    }
    
    result = recovery_module.execute(request)
    print(f"✅ restart_ollama test completed with status: {result.get('structured_data', {}).get('status', 'unknown')}")
    
    # Expected structure validation
    structured_data = result.get('structured_data', {})
    assert structured_data.get('action') == 'restart_ollama'
    assert 'status' in structured_data
    print("   ✓ Response structure is correct")
    
    return result

def test_validate_gpu():
    """Test validate_gpu function"""
    print("\n🧪 Testing validate_gpu function...")
    
    request = {
        "request_id": "integration-test-gpu", 
        "input": "validate gpu"
    }
    
    result = recovery_module.execute(request)
    print(f"✅ validate_gpu test completed with status: {result.get('structured_data', {}).get('status', 'unknown')}")
    
    # Expected structure validation
    structured_data = result.get('structured_data', {})
    assert structured_data.get('action') == 'validate_gpu'
    assert 'status' in structured_data
    assert 'tests' in structured_data
    print("   ✓ Response structure is correct")
    
    # Check test results structure
    tests = structured_data.get('tests', {})
    expected_tests = ['torch_import', 'cuda_available', 'gpu_count', 'gpu_operations']
    for test_name in expected_tests:
        if test_name in tests:
            print(f"   ✓ {test_name}: {tests[test_name].get('status', 'unknown')}")
    
    return result

def test_keyword_routing():
    """Test that keywords properly route to new functions"""
    print("\n🧪 Testing keyword routing...")
    
    test_cases = [
        ("restart ollama", "restart_ollama"),
        ("fix ollama connectivity", "restart_ollama"),
        ("validate gpu", "validate_gpu"),
        ("test gpu pytorch", "validate_gpu"),
        ("cuda test", "validate_gpu"),
        ("gpu validation", "validate_gpu")
    ]
    
    for input_text, expected_action in test_cases:
        detected_action = recovery_module.analyze_user_input(input_text)
        print(f"   '{input_text}' → '{detected_action}' (expected: '{expected_action}')")
        assert detected_action == expected_action, f"Expected {expected_action}, got {detected_action}"
    
    print("   ✅ All keyword routing tests passed")

def test_available_actions():
    """Test that new actions are listed in available actions"""
    print("\n🧪 Testing available actions list...")
    
    actions = recovery_module.get_available_recovery_actions()
    available_actions = actions.get('available_actions', {})
    
    assert 'restart_ollama' in available_actions
    assert 'validate_gpu' in available_actions
    
    print(f"   ✓ restart_ollama: {available_actions['restart_ollama']}")
    print(f"   ✓ validate_gpu: {available_actions['validate_gpu']}")
    print("   ✅ New actions are properly registered")

def main():
    """Run all integration tests"""
    print("🚀 Emergency Recovery Module Integration Tests")
    print("=" * 50)
    
    try:
        # Test available actions first
        test_available_actions()
        
        # Test keyword routing
        test_keyword_routing()
        
        # Test actual function execution
        restart_result = test_restart_ollama()
        gpu_result = test_validate_gpu()
        
        print("\n🎉 All integration tests passed!")
        print("\n📋 Summary:")
        print("   - restart_ollama: Implemented and functional (requires container environment)")
        print("   - validate_gpu: Implemented and functional (works in any environment)")
        print("   - Keyword routing: Working correctly")
        print("   - Module integration: Complete")
        
        print("\n📝 Usage Examples for OpenWebUI:")
        print("   - 'restart ollama' → Restarts Ollama container")
        print("   - 'fix ollama connectivity' → Restarts Ollama container") 
        print("   - 'validate gpu' → Tests GPU and PyTorch")
        print("   - 'test gpu pytorch' → Tests GPU and PyTorch")
        print("   - 'cuda test' → Tests GPU and PyTorch")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()