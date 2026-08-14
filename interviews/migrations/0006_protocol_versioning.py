import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def initialise_protocol_versions(apps, schema_editor):
    Protocol = apps.get_model("interviews", "Protocol")
    InterviewSession = apps.get_model("interviews", "InterviewSession")

    now = django.utils.timezone.now()
    for protocol in Protocol.objects.all().iterator():
        protocol.family_id = uuid.uuid4()
        if InterviewSession.objects.filter(protocol_id=protocol.pk).exists():
            protocol.status = "locked"
            protocol.locked_at = now
        protocol.save(update_fields=["family_id", "status", "locked_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("interviews", "0005_interviewsession_access_token"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="protocol",
            options={"ordering": ["title", "-version"]},
        ),
        migrations.AddField(
            model_name="protocol",
            name="family_id",
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="protocol",
            name="locked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="protocol",
            name="parent_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="derived_versions",
                to="interviews.protocol",
            ),
        ),
        migrations.AddField(
            model_name="protocol",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("locked", "Locked")],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="protocol",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="protocol",
            name="version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(
            initialise_protocol_versions,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="protocol",
            name="family_id",
            field=models.UUIDField(
                db_index=True,
                default=uuid.uuid4,
                editable=False,
            ),
        ),
        migrations.AddConstraint(
            model_name="protocol",
            constraint=models.UniqueConstraint(
                fields=("family_id", "version"),
                name="unique_protocol_family_version",
            ),
        ),
    ]
