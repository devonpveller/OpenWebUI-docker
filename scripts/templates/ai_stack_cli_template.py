#!/usr/bin/env python3
"""
AI Stack CLI Template

Template for creating CLI-based pipe scripts that integrate with OpenWebUI
through subprocess execution mode.

Usage: This script receives JSON input via stdin and outputs JSON results.
"""

import sys
import json
import time
from typing import Dict, Any

def process_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process the input payload and return results"""
    user_input = payload.get("input", "")
    messages = payload.get("messages", [])
    gpu_available = payload.get("gpu_available", False)
    workspace_context = payload.get("workspace_context", "unknown")
    
    # Your CLI processing logic here
    # This is where you implement your specific functionality
    
    # Example processing
    processed_data = {
        "original_input": user_input,
        "processed_input": user_input.strip().lower(),
        "word_count": len(user_input.split()),
        "message_count": len(messages),
        "gpu_status": "available" if gpu_available else "not_available",
        "workspace": workspace_context,
        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    
    # Add your custom processing here
    # Example: analyze sentiment, process data, call external APIs, etc.
    
    return {
        "status": "success",
        "data": processed_data,
        "template": "ai_stack_cli_template",
        "version": "1.0.0"
    }

def handle_error(error: Exception) -> Dict[str, Any]:
    """Handle errors and return structured error response"""
    return {
        "status": "error",
        "error": str(error),
        "error_type": type(error).__name__,
        "template": "ai_stack_cli_template"
    }

def main():
    """Main CLI entry point"""
    try:
        # Read JSON input from stdin
        input_data = sys.stdin.read()
        if not input_data.strip():
            raise ValueError("No input data received")
        
        payload = json.loads(input_data)
        
        # Process the input
        result = process_input(payload)
        
        # Output JSON result
        print(json.dumps(result, indent=2))
        
    except json.JSONDecodeError as e:
        error_result = handle_error(e)
        error_result["error_details"] = "Invalid JSON input"
        print(json.dumps(error_result, indent=2))
        sys.exit(1)
        
    except Exception as e:
        error_result = handle_error(e)
        print(json.dumps(error_result, indent=2))
        sys.exit(1)

if __name__ == "__main__":
    main()