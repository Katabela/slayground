from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "sg"
urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),

    path("classes/", views.class_list, name="class_list"),
    path("classes/<int:pk>/", views.class_detail, name="class_detail"),
    path("book/<int:session_id>/", views.book_session, name="book_session"),

    path("content/", views.content_hub, name="content_hub"),

    path("calendar/", views.calendar_view, name="calendar"),
    path("calendar/events/", views.calendar_events, name="calendar_events"),

    path("staff/quick-session/", views.quick_create_session, name="quick_create_session"),

    path("slayvents/", views.slayvents, name="slayvents"),

    path("slaybrations/", views.slaybrations_list, name="slaybrations_list"),
    path("slaybrations/<slug:slug>/", views.slaybrations_detail, name="slaybrations_detail"),
    path("slaybrations/<slug:slug>/register/", views.slaybrations_register, name="slaybrations_register"),

    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/signup/", views.signup, name="signup"),
]
