from django import template

register = template.Library()

# Icon mapping dictionary
ICON_MAPPING = {
    'admission': '🎓',
    'academics': '📚',
    'fee payment': '💳',
    'library': '📚',
    'examination': '📋',
    'accounts': '💰',
    'it support': '💻',
    'hr services': '👥',
    'transport': '🚌',
    'hostel': '🏠',
    'medical': '🏥',
    'security': '🛡️',
    'maintenance': '🔧',
    'counseling': '💬',
    'sports': '⚽',
    'placement': '💼',
    'scholarship': '奖学',
    'transcript': '📜',
    'certificate': '📄',
    'clearance': '✅',
    'complaint': '⚠️',
    'feedback': '📝',
    'book': '📖',
    'fine': '💰',
    'renewal': '🔄',
    'application': '📋',
    'request': '📤',
    'enquiry': '❓',
    'help': '❓',
    'support': '🛠️',
    'service': '⚙️',
    'general': '🎫',
    'default': '🎫'
}

@register.filter
def icon_for_service(service_name):
    """
    Returns an appropriate icon based on the service name.
    The comparison is case-insensitive and matches partial strings.
    """
    service_lower = service_name.lower().strip()
    
    # Check for exact matches first
    if service_lower in ICON_MAPPING:
        return ICON_MAPPING[service_lower]
    
    # Check for partial matches in service names
    for service_type, icon in ICON_MAPPING.items():
        if service_type in service_lower and service_type != 'default':
            return icon
    
    # Return default icon if no match found
    return ICON_MAPPING['default']