import sys, json
sys.path.append('/host_project/core')
from router import router, main

# Test comprehensive routing scenarios
test_scenarios = [
    # GPU queries
    ('Check my GPU status', 'gpu-status'),
    ('CUDA availability check', 'gpu-status'), 
    ('RTX memory usage', 'gpu-status'),
    
    # System health queries
    ('System status check', 'system-health'),
    ('Overall system health', 'system-health'),
    ('Are services running?', 'system-health'),
    
    # Emergency recovery
    ('Fix network issues', 'emergency-recovery'),
    ('Emergency shutdown', 'emergency-recovery'),
    ('System recovery', 'emergency-recovery'),
    
    # Help requests
    ('Show available commands', 'help-system'),
    ('What can you do?', 'help-system'),
    ('Help with GPU', 'help-system')
]

print('🧪 AI Stack Unified Router - Comprehensive Test')
print('=' * 60)

# Test routing function
for query, expected in test_scenarios:
    target = router._analyze_input_for_routing(query)
    status = '✅' if expected == target else '❌'
    print(f'{status} "{query[:30]:<30}" → {target:<18}')

# Test router status
print('\n📊 Router Status Test:')
result = main({'input': 'help', 'user_id': 'test'})
status = '✅' if result.get('status') in ['ok', 'error'] else '❌'
module_count = len(router.registry.get_ready_modules())
print(f'{status} Router Status → {module_count} modules loaded')