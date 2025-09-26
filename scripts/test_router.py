import sys, json
sys.path.append('/host_scripts')
from ai_pipes import ai_stack_router

# Test comprehensive routing scenarios
test_scenarios = [
    # GPU queries
    ('Check my GPU status', 'gpu_status'),
    ('CUDA availability check', 'gpu_status'), 
    ('RTX memory usage', 'gpu_status'),
    
    # Recovery scenarios
    ('Tailscale is down', 'emergency_recovery'),
    ('Network connectivity problems', 'emergency_recovery'),
    ('System recovery needed', 'emergency_recovery'),
    
    # Health monitoring
    ('How is my system doing?', 'system_health'),
    ('Docker container status', 'system_health'),
    
    # Tools and help
    ('What tools are available?', 'custom_tools'),
    ('Help me', 'help')
]

print('🧪 AI Stack Unified Router - Comprehensive Test')
print('=' * 60)

router_instance = ai_stack_router.AIStackRouter()

for query, expected in test_scenarios:
    target, confidence = router_instance.analyze_user_input(query)
    status = '✅' if expected in target else '❌'
    print(f'{status} "{query[:30]:<30}" → {target:<18} ({confidence:.2f})')

# Test router status
print('\n📊 Router Status Test:')
result = ai_stack_router.main({'input': ''})
status = '✅' if 'operational' in result.get('status', '') else '❌'
print(f'{status} Router Status → {result.get("module_count", 0)} modules loaded')