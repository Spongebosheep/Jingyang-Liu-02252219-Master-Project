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
from django.urls import path

from interviews import views

urlpatterns = [
    path("admin/", admin.site.urls),

    path("", views.overview, name="overview"),

    path("stakeholders/", views.stakeholder_list, name="stakeholder_list"),
    path("stakeholders/<str:participant_id>/", views.stakeholder_detail, name="stakeholder_detail"),

    path("protocols/<slug:slug>/", views.protocol_detail, name="protocol_detail"),

    path("interviews/", views.interview_sessions, name="interview_sessions"),

    path("interview/<str:participant_id>/consent/", views.interview_consent, name="interview_consent"),
    path("interview/<str:participant_id>/session/", views.interview_session, name="interview_session"),
    path("interview/<str:participant_id>/completed/", views.interview_completed, name="interview_completed"),

    path("outputs/", views.outputs_home, name="outputs_home"),
    path("outputs/<str:session_code>/", views.output_detail, name="output_detail"),

    path("output-quality/", views.output_quality, name="output_quality"),

    path("output-quality/export/", views.export_evidence_record, name="export_evidence_record"),
]
