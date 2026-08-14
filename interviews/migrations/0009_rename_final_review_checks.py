from django.db import migrations, models


def clarify_output_description(apps, schema_editor):
    Protocol = apps.get_model("interviews", "Protocol")
    Protocol.objects.filter(
        output_description="Transcript + evidence by protocol topic"
    ).update(
        output_description="Typed transcript + reviewed extracts by protocol topic"
    )
    StructuredDigestItem = apps.get_model("interviews", "StructuredDigestItem")
    for item in StructuredDigestItem.objects.filter(
        generated_text__startswith="Participant evidence:"
    ):
        item.generated_text = item.generated_text.replace(
            "Participant evidence:", "Typed response extract:", 1
        )
        item.save(update_fields=["generated_text"])


def restore_output_description(apps, schema_editor):
    Protocol = apps.get_model("interviews", "Protocol")
    Protocol.objects.filter(
        output_description="Typed transcript + reviewed extracts by protocol topic"
    ).update(
        output_description="Transcript + evidence by protocol topic"
    )
    StructuredDigestItem = apps.get_model("interviews", "StructuredDigestItem")
    for item in StructuredDigestItem.objects.filter(
        generated_text__startswith="Typed response extract:"
    ):
        item.generated_text = item.generated_text.replace(
            "Typed response extract:", "Participant evidence:", 1
        )
        item.save(update_fields=["generated_text"])


class Migration(migrations.Migration):

    dependencies = [
        ("interviews", "0008_agentdecision_covered_information"),
    ]

    operations = [
        migrations.RenameField(
            model_name="reviewdecision",
            old_name="summary_grounded_in_transcript",
            new_name="source_links_checked",
        ),
        migrations.RenameField(
            model_name="reviewdecision",
            old_name="no_unsupported_interpretation",
            new_name="participant_meaning_preserved",
        ),
        migrations.RenameField(
            model_name="reviewdecision",
            old_name="no_medical_or_diagnostic_advice",
            new_name="protocol_boundaries_respected",
        ),
        migrations.RenameField(
            model_name="reviewdecision",
            old_name="participant_safety_respected",
            new_name="participant_controls_respected",
        ),
        migrations.AlterField(
            model_name="protocol",
            name="output_description",
            field=models.CharField(
                default="Typed transcript + reviewed extracts by protocol topic",
                max_length=200,
            ),
        ),
        migrations.RunPython(
            clarify_output_description,
            restore_output_description,
        ),
    ]
