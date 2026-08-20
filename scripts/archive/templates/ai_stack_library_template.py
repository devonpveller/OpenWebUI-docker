"""
AI Stack Library Template

Template for creating library-based pipe scripts that integrate with OpenWebUI
through import execution mode.

Usage: This script is imported and the main() function is called with payload data.
"""

import json
import time
from typing import Dict, Any, List, Optional

# Try to import torch for GPU functionality
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

def process_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process input data with GPU awareness"""
    input_text = payload.get("input", "")
    gpu_available = payload.get("gpu_available", False)
    messages = payload.get("messages", [])
    
    # Basic text processing
    processed = {
        "original": input_text,
        "processed": input_text.strip().lower(),
        "word_count": len(input_text.split()),
        "character_count": len(input_text),
        "lines": input_text.count('\n') + 1 if input_text else 0
    }
    
    # Add GPU-specific processing if available
    if gpu_available and TORCH_AVAILABLE:
        processed["gpu_accelerated"] = True
        processed["torch_version"] = torch.__version__
        processed["cuda_device_count"] = torch.cuda.device_count()
        
        # Example: Simple tensor operations (placeholder for real GPU work)
        try:
            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            processed["computation_device"] = str(device)
            
            # Add your GPU-accelerated processing here
            # Example: text embeddings, model inference, etc.
            
        except Exception as e:
            processed["gpu_error"] = str(e)
    else:
        processed["gpu_accelerated"] = False
    
    return processed

def analyze_messages(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze message history for context"""
    if not messages:
        return {"message_analysis": "No messages to analyze"}
    
    analysis = {
        "total_messages": len(messages),
        "user_messages": sum(1 for msg in messages if msg.get("role") == "user"),
        "assistant_messages": sum(1 for msg in messages if msg.get("role") == "assistant"),
        "recent_user_message": None
    }
    
    # Find most recent user message
    for msg in reversed(messages):
        if msg.get("role") == "user":
            analysis["recent_user_message"] = msg.get("content", "")[:100]  # First 100 chars
            break
    
    return analysis

def check_system_status(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Check AI stack system status"""
    status = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workspace": payload.get("workspace_context", "unknown"),
        "torch_available": TORCH_AVAILABLE
    }
    
    if TORCH_AVAILABLE:
        status.update({
            "cuda_available": torch.cuda.is_available(),
            "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0
        })
        
        if torch.cuda.is_available():
            status["current_device"] = torch.cuda.get_device_name()
    
    return status

def perform_custom_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Perform custom task based on input"""
    user_input = payload.get("input", "").lower()
    
    # Example task routing based on keywords
    if "analyze" in user_input:
        return {
            "task": "analysis",
            "result": analyze_messages(payload.get("messages", []))
        }
    elif "status" in user_input or "health" in user_input:
        return {
            "task": "status_check",
            "result": check_system_status(payload)
        }
    elif "process" in user_input or "transform" in user_input:
        return {
            "task": "data_processing",
            "result": process_data(payload)
        }
    else:
        return {
            "task": "general",
            "result": "No specific task detected. Available tasks: analyze, status, process"
        }

def main(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Main entry point for library-based scripts"""
    try:
        # Process the input data
        processed_data = process_data(payload)
        
        # Perform system checks
        system_status = check_system_status(payload)
        
        # Execute custom task
        custom_task = perform_custom_task(payload)
        
        # Compile results
        result = {
            "status": "success",
            "template": "ai_stack_library_template",
            "version": "1.0.0",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "data": {
                "processed": processed_data,
                "system": system_status,
                "task": custom_task
            },
            "summary": f"Processed {processed_data.get('word_count', 0)} words with {system_status.get('cuda_available', 'unknown')} GPU status"
        }
        
        return result
        
    except Exception as e:
        return {
            "status": "failed",
            "template": "ai_stack_library_template",
            "error": str(e),
            "error_type": type(e).__name__,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

# Additional utility functions that can be called directly

def get_template_info() -> Dict[str, Any]:
    """Get information about this template"""
    return {
        "name": "AI Stack Library Template",
        "version": "1.0.0",
        "description": "Template for creating library-based pipe scripts",
        "features": [
            "GPU awareness with PyTorch integration",
            "Message history analysis",
            "System status checking",
            "Custom task routing",
            "Error handling"
        ],
        "usage": "Import this module and call main(payload) or specific functions"
    }

def validate_payload(payload: Dict[str, Any]) -> bool:
    """Validate payload structure"""
    required_fields = ["input"]
    return all(field in payload for field in required_fields)