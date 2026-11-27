"""
URL routing for the notifications bell.

Included from config/urls.py as:
    path('api/notifications/', include('apps.notifications.urls'))

NOTE: no `app_name` is set on purpose. The project URLconf does not use a
namespace, so templates and reverse() calls must use the bare names below:

    {% url 'notifications_list' %}
    {% url 'notifications_count' %}
    {% url 'notifications_read' notification.id %}
    {% url 'notifications_delete' notification.id %}
    {% url 'notifications_clear_all' %}

If you later want namespaced reverses (`notifications:list`), add
`app_name = 'notifications'` at the top and update every template that
currently reverses these names.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.list_notifications, name='notifications_list'),
    path('count/', views.count_notifications, name='notifications_count'),
    path('<int:pk>/read/', views.mark_read, name='notifications_read'),
    path('<int:pk>/delete/', views.delete_notification, name='notifications_delete'),
    path('clear-all/', views.clear_all, name='notifications_clear_all'),
]