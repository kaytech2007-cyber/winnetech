class PrivateResponseCacheControlMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if getattr(request, 'user', None) and request.user.is_authenticated:
            response['Cache-Control'] = 'private, no-store, no-cache, max-age=0'
            response['Pragma'] = 'no-cache'
        return response
