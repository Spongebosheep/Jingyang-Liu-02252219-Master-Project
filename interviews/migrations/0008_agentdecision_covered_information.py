from django.db import migrations, models


def update_output_description(apps, schema_editor):
    Protocol = apps.get_model("interviews", "Protocol")
    Protocol.objects.filter(
        output_description="Transcript + structured summary"
    ).update(output_description="Transcript + evidence by protocol topic")


def restore_output_description(apps, schema_editor):
    Protocol = apps.get_model("interviews", "Protocol")
    Protocol.objects.filter(
        output_description="Transcript + evidence by protocol topic"
    ).update(output_description="Transcript + structured summary")


class Migration(migrations.Migration):

    dependencies = [
        ("interviews", "0007_structured_digest_and_reviewer_identity"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdecision",
            name="covered_information",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="protocol",
            name="output_description",
            field=models.CharField(
                default="Transcript + evidence by protocol topic",
                max_length=200,
            ),
        ),
        migrations.RunPython(
            update_output_description,
            restore_output_description,
        ),
    ]
