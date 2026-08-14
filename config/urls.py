"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from interviews import views

urlpatterns = [
    path("admin/", admin.site.urls),

    path(
        "login/",
        auth_views.LoginView.as_view(template_name="interviews/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("", views.overview, name="overview"),

    path("stakeholders/", views.stakeholder_list, name="stakeholder_list"),
    path("stakeholders/add/", views.stakeholder_create, name="stakeholder_create"),
    path("stakeholders/<str:participant_id>/", views.stakeholder_detail, name="stakeholder_detail"),

    path("protocols/", views.protocol_list, name="protocol_list"),
    path("protocols/new/", views.protocol_create, name="protocol_create"),
    path("protocols/<slug:slug>/", views.protocol_detail, name="protocol_detail"),
    path("protocols/<slug:slug>/edit/", views.protocol_edit, name="protocol_edit"),
    path("protocols/<slug:slug>/lock/", views.protocol_lock, name="protocol_lock"),
    path("protocols/<slug:slug>/duplicate/", views.protocol_duplicate, name="protocol_duplicate"),

    path("interviews/", views.interview_sessions, name="interview_sessions"),
    path("interviews/add/", views.interview_session_create, name="interview_session_create"),

    path("interview/<uuid:access_token>/consent/", views.interview_consent, name="interview_consent"),
    path("interview/<uuid:access_token>/session/", views.interview_session, name="interview_session"),
    path("interview/<uuid:access_token>/completed/", views.interview_completed, name="interview_completed"),

    path("outputs/", views.outputs_home, name="outputs_home"),
    path("outputs/<str:session_code>/", views.output_detail, name="output_detail"),
    path(
        "outputs/<str:session_code>/digest/<int:item_id>/review/",
        views.review_digest_item,
        name="review_digest_item",
    ),

    path("output-quality/", views.output_quality, name="output_quality"),

    path("output-quality/export/", views.export_evidence_record, name="export_evidence_record"),
]
