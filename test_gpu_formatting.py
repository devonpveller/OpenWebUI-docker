#!/usr/bin/env python3
"""
Test the OpenWebUI pipe template formatting with actual GPU status data
"""
import sys
import os
sys.path.append('/host_scripts' if os.path.exists('/host_scripts') else './scripts')

# Simulate the pipe template formatting
def format_json_result(result):
    """Test version of the formatting function"""
    try:
        if isinstance(result, dict):
            formatted_lines = []
            
            # Handle status/service information
            if "service" in result:
                formatted_lines.append(f"**{result['service']}**")
            
            # Handle basic service responses (like no-input GPU status)
            if "service" in result and "quick_status" in result and isinstance(result["quick_status"], str):
                formatted_lines.append(f"**{result['service']}**")
                formatted_lines.append(f"Status: {result['quick_status']}")
                if result.get("usage_tip"):
                    formatted_lines.append(f"\n*Tip: {result['usage_tip']}*")
            
            # Handle GPU status details (from gpu_status_pipe.py)
            if "gpu_status" in result and isinstance(result["gpu_status"], dict):
                gpu_data = result["gpu_status"]
                formatted_lines.append(f"\n**GPU Status Details:**")
                if gpu_data.get("status"):
                    formatted_lines.append(f"Status: {gpu_data['status']}")
                if gpu_data.get("devices") and len(gpu_data["devices"]) > 0:
                    device = gpu_data["devices"][0]  # Show first device
                    formatted_lines.append(f"Device: {device.get('name', 'Unknown')}")
                    formatted_lines.append(f"Memory: {device.get('memory_allocated_gb', 0)} GB used / {device.get('total_memory_gb', 0)} GB total")
            
            # Handle recommendations
            if "recommendations" in result and isinstance(result["recommendations"], dict):
                rec_data = result["recommendations"]
                if rec_data.get("status"):
                    formatted_lines.append(f"\n**Recommendations:**")
                    formatted_lines.append(f"Status: {rec_data['status']}")
                    if rec_data.get("optimization_tips"):
                        for tip in rec_data["optimization_tips"][:2]:  # Show first 2 tips
                            formatted_lines.append(f"• {tip}")
            
            # Add timestamp if available
            if "timestamp" in result:
                formatted_lines.append(f"\n*Updated: {result['timestamp']}*")
            
            if formatted_lines:
                return '\n'.join(formatted_lines)
        
        return str(result)
    except Exception as e:
        return f"Format error: {str(e)}"

if __name__ == "__main__":
    # Test with basic GPU response
    from ai_pipes import gpu_status_pipe
    
    print("=== Testing Basic GPU Status (no input) ===")
    basic_result = gpu_status_pipe.main({'input': ''})
    formatted_basic = format_json_result(basic_result)
    print(formatted_basic)
    
    print("\n=== Testing Detailed GPU Status (with input) ===")
    detailed_result = gpu_status_pipe.main({'input': 'check gpu status'})
    formatted_detailed = format_json_result(detailed_result)
    print(formatted_detailed)