from django.urls import path
from routing.views import plan_route

urlpatterns = [path("api/route/", plan_route, name="route-plan")]
