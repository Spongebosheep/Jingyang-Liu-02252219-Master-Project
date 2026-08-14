import uuid

from django.db import migrations, models


def populate_session_access_tokens(apps, schema_editor):
    InterviewSession = apps.get_model("interviews", "InterviewSession")

    for session in InterviewSession.objects.filter(access_token__isnull=True).iterator():
        access_token = uuid.uuid4()
        while InterviewSession.objects.filter(access_token=access_token).exists():
            access_token = uuid.uuid4()
        session.access_token = access_token
        session.save(update_fields=["access_token"])


class Migration(migrations.Migration):
    dependencies = [
        ("interviews", "0004_reviewdecision_limitations_and_missing_information_visible"),
    ]

    operations = [
        migrations.AddField(
            model_name="interviewsession",
            name="access_token",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_session_access_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="interviewsession",
            name="access_token",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
    ]
