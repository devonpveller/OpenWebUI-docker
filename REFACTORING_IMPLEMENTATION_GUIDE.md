# AI Stack Refactoring Implementation Guide

## 🚀 Getting Started with the Refactored Architecture

This guide walks you through implementing the new manifest-driven AI Stack architecture based on the comprehensive refactoring guide.

### Prerequisites

- AI Stack running with Docker Compose
- Python 3.8+ with container access
- OpenWebUI admin access
- Basic understanding of the existing pipe system

## 📋 Implementation Overview

The refactoring introduces:

- **Manifest-driven modules** with explicit contracts
- **Schema validation** for all communications
- **Unified router** with intelligent routing
- **Comprehensive observability** and error handling
- **Automated testing and validation** tools
- **Migration automation** from legacy pipes

## 🎯 Quick Start (Automated)

### Option 1: Complete Automated Refactoring

```bash
# Navigate to AI Stack directory
cd /d/Open\ WebUI/ai-stack

# Run the complete refactoring process
python tools/refactor_orchestrator.py

# This will execute all 5 phases:
# Phase 0: Analysis & Planning
# Phase 1: Core Infrastructure  
# Phase 2: Module System
# Phase 3: Migration & Integration
# Phase 4: Validation & Testing
```

### Option 2: Phase-by-Phase Implementation

```bash
# Phase 0: Analyze current system
python tools/refactor_orchestrator.py --phase 0

# Phase 1: Set up core infrastructure  
python tools/refactor_orchestrator.py --phase 1

# Phase 2: Initialize module system
python tools/refactor_orchestrator.py --phase 2

# Phase 3: Migrate legacy modules
python tools/refactor_orchestrator.py --phase 3

# Phase 4: Validate and test
python tools/refactor_orchestrator.py --phase 4
```

## 🔧 Manual Implementation Steps

### Step 1: Create Directory Structure

```bash
# Create required directories
mkdir -p schemas core modules tools

# Copy schema files (already created)
# schemas/
#   ├── request_envelope.schema.json
#   ├── module_result.schema.json  
#   └── module_manifest.schema.json
```

### Step 2: Install Core Infrastructure

The core router (`core/router.py`) provides:
- Schema validation
- Module registry and discovery
- Intelligent routing
- Legacy fallback support

### Step 3: Migrate Legacy Modules

```bash
# Analyze existing modules
python tools/migration_tool.py --analyze-only

# Migrate all modules
python tools/migration_tool.py --all

# Or migrate specific module
python tools/migration_tool.py --module gpu_status_pipe.py
```

### Step 4: Update OpenWebUI Integration

Replace the existing unified pipe function with the new adapter:

1. Go to **OpenWebUI Admin → Functions**
2. Find existing "AI Stack Unified System" function
3. Replace content with `core/openwebui_adapter.py`
4. Save and test

### Step 5: Validate System

```bash
# Comprehensive validation
python tools/validation_tool.py --comprehensive

# Test specific module
python tools/validation_tool.py --module gpu-status

# Router-only testing
python tools/validation_tool.py --router-only
```

## 📦 Creating New Modules

### Using the Scaffolding Generator

```bash
# Interactive module creation
python tools/scaffold_generator.py --interactive

# From configuration file
python tools/scaffold_generator.py --config my_module_config.json
```

### Manual Module Creation

1. Create module directory: `modules/my-module/`
2. Create manifest: `module.manifest.json`
3. Implement service: `service/my_module.py`
4. Add documentation: `docs/README.md`

Example manifest structure:
```json
{
  "name": "My Custom Module",
  "slug": "my-module", 
  "version": "1.0.0",
  "entry": {
    "kind": "cli",
    "path": "service/my_module.py",
    "function": "main"
  },
  "capabilities": ["system_monitoring"],
  "schema": {
    "input": { ... },
    "output": { ... }
  }
}
```

## 🧪 Testing and Validation

### Module Testing

```bash
# Test module health
python modules/gpu-status/service/gpu_status.py --health

# Test module execution
echo '{"request_id": "test", "input": "status"}' | python modules/gpu-status/service/gpu_status.py
```

### Router Testing

```bash
# Test router initialization
python core/router.py

# Test routing with query
echo '{"input": "gpu status", "user_id": "test"}' | python core/router.py
```

### Integration Testing

```bash
# Test OpenWebUI adapter
python core/openwebui_adapter.py

# Regression testing
python tools/validation_tool.py --regression-only
```

## 📊 Monitoring and Observability

### Logging

All components use structured logging:
```python
logger.info("Module executed", extra={
    "request_id": request_id,
    "module_id": "gpu-status", 
    "execution_time_ms": 150
})
```

### Metrics and Health

- Module health endpoints: `--health`
- Router status: Available modules and routing stats
- Execution diagnostics: Timing, resource usage, error rates

### Error Handling

- Structured error responses with error codes
- Retry guidance (retriable vs non-retriable errors)
- Comprehensive error context and debugging information

## 🔄 Migration Strategy

### Gradual Rollout

1. **Phase 1**: Deploy refactored system alongside legacy
2. **Phase 2**: Test with subset of users
3. **Phase 3**: Gradually increase traffic to new system
4. **Phase 4**: Deprecate legacy system
5. **Phase 5**: Remove legacy components

### Rollback Plan

- Legacy system remains functional during migration
- Instant rollback via OpenWebUI function replacement
- Module-by-module rollback capability
- Comprehensive backup of configuration and data

## 🛠️ Troubleshooting

### Common Issues

**Module Not Found**
```bash
# Check module registry
python core/router.py

# Verify module structure
ls -la modules/module-name/
```

**Schema Validation Errors**
```bash
# Validate manifest
python tools/validation_tool.py --module module-name

# Test schema compliance
python -c "import json; print(json.load(open('modules/module-name/module.manifest.json')))"
```

**Router Initialization Failures**
```bash
# Check dependencies
python -c "import sys; print(sys.path)"

# Test imports
python -c "from core.router import AIStackRouter; print('Router import successful')"
```

### Debug Mode

Enable debug mode in OpenWebUI adapter:
```python
class Valves(BaseModel):
    DEBUG_MODE: bool = Field(default=True)
```

## 📈 Performance Optimization

### Caching

- Module result caching based on input hash
- Schema validation caching
- Module registry caching with hot-reload

### Resource Management

- Per-module resource limits
- Concurrent request limiting  
- Memory and CPU monitoring

### Scalability

- Stateless router design
- Multiple router instances
- Load balancing and health-aware routing

## 🔐 Security Considerations

### Module Isolation

- Execution environment isolation (venv/container)
- Resource limits and timeouts
- Capability-based security model

### Input Validation

- JSON Schema validation
- Input sanitization and size limits
- User permission checking

### Audit and Compliance

- Complete request/response logging
- User action tracking
- Security event monitoring

## 📚 Advanced Usage

### Custom Execution Adapters

Create custom adapters for different execution environments:
```python
class CustomAdapter:
    def execute(self, manifest, request):
        # Custom execution logic
        pass
```

### Schema Extensions

Extend base schemas for domain-specific requirements:
```json
{
  "allOf": [
    {"$ref": "module_manifest.schema.json"},
    {
      "properties": {
        "custom_field": {"type": "string"}
      }
    }
  ]
}
```

### Agent Orchestration

Build on the router for multi-step agent workflows:
```python
class AgentOrchestrator:
    def plan_and_execute(self, user_goal):
        # Decompose goal into steps
        # Route each step to appropriate module
        # Aggregate and return results
        pass
```

## 🎉 Migration Completion

### Verification Checklist

- [ ] All legacy modules migrated successfully
- [ ] Schema validation passing
- [ ] Router routing correctly  
- [ ] OpenWebUI integration working
- [ ] Performance meets expectations
- [ ] Error handling functioning
- [ ] Monitoring and logging operational

### Next Steps

1. **User Training**: Update documentation and user guides
2. **Performance Monitoring**: Set up alerts and dashboards  
3. **Iterative Improvement**: Collect feedback and enhance
4. **Advanced Features**: Implement agent orchestration and learning
5. **Community**: Share improvements back to AI Stack community

## 📞 Support

If you encounter issues during refactoring:

1. **Check Logs**: Review detailed execution logs
2. **Run Diagnostics**: Use validation tools for troubleshooting
3. **Consult Guide**: Reference the comprehensive refactoring guide
4. **Community Help**: Seek assistance from AI Stack community

The refactored architecture provides a solid foundation for scalable, maintainable AI Stack functionality while maintaining backward compatibility and providing clear migration paths.