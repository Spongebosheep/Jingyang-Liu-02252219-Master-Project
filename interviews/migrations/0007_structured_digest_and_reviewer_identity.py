from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("interviews", "0006_protocol_versioning"),
    ]

    operations = [
        migrations.AddField(
            model_name="reviewdecision",
            name="reviewed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="reviewdecision",
            name="reviewed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="purrstone_review_decisions",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="reviewdecision",
            name="reviewer_name_snapshot",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.CreateModel(
            name="StructuredDigestItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("section_index", models.PositiveIntegerField()),
                ("section_code", models.CharField(blank=True, max_length=100)),
                ("section_title", models.CharField(max_length=200)),
                ("label", models.CharField(max_length=200)),
                ("coverage_status", models.CharField(choices=[("answered", "Answered"), ("partial", "Partial"), ("skipped", "Skipped"), ("stopped", "Stopped"), ("not_reached", "Not reached")], default="not_reached", max_length=30)),
                ("missing_information", models.JSONField(blank=True, default=list)),
                ("generated_text", models.TextField()),
                ("generation_method", models.CharField(default="deterministic_extractive_v1", editable=False, max_length=80)),
                ("review_status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("edited", "Edited"), ("excluded", "Excluded")], default="pending", max_length=30)),
                ("reviewed_text", models.TextField(blank=True)),
                ("reviewer_comment", models.TextField(blank=True)),
                ("reviewer_name_snapshot", models.CharField(blank=True, max_length=200)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="purrstone_digest_items", to=settings.AUTH_USER_MODEL)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="digest_items", to="interviews.interviewsession")),
                ("source_messages", models.ManyToManyField(blank=True, related_name="structured_digest_items", to="interviews.message")),
            ],
            options={"ordering": ["section_index", "id"]},
        ),
        migrations.CreateModel(
            name="DigestReviewEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("previous_status", models.CharField(blank=True, max_length=30)),
                ("new_status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("edited", "Edited"), ("excluded", "Excluded")], max_length=30)),
                ("previous_text", models.TextField(blank=True)),
                ("new_text", models.TextField(blank=True)),
                ("comment", models.TextField(blank=True)),
                ("reviewer_name_snapshot", models.CharField(max_length=200)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("digest_item", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_events", to="interviews.structureddigestitem")),
                ("reviewer", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="purrstone_digest_review_events", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="structureddigestitem",
            constraint=models.UniqueConstraint(fields=("session", "section_index"), name="unique_digest_item_per_session_section"),
        ),
    ]
