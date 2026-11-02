import re

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]

# In DEBUG, Django serves both. In production, static files use WhiteNoise; user uploads
# still use MEDIA_URL relative paths unless Cloudinary is enabled (absolute URLs).
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
else:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    media_prefix = settings.MEDIA_URL.lstrip('/').rstrip('/')
    if media_prefix:
        urlpatterns += [
            re_path(
                rf'^{re.escape(media_prefix)}/(?P<path>.*)$',
                serve,
                {'document_root': settings.MEDIA_ROOT},
            ),
        ]
