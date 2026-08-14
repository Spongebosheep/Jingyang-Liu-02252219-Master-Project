from django.db import migrations, models


AGENT_FORWARD = {
    "sufficient": "covered",
    "partial": "partially_covered",
    "vague": "unclear",
    "too_short": "unclear",
    "off_topic": "off_topic",
    "skipped": "not_assessed",
    "stopped": "not_assessed",
    "safety_boundary": "not_assessed",
}

DIGEST_FORWARD = {
    "answered": "covered",
    "partial": "partially_covered",
    "skipped": "not_assessed",
    "stopped": "not_assessed",
    "not_reached": "not_assessed",
}


def forwards(apps, schema_editor):
    InterviewSession = apps.get_model("interviews", "InterviewSession")
    AgentDecision = apps.get_model("interviews", "AgentDecision")
    StructuredDigestItem = apps.get_model("interviews", "StructuredDigestItem")

    InterviewSession.objects.filter(
        consent_confirmed=True,
        consent_confirmed_at__isnull=True,
    ).update(consent_confirmed_at=models.F("started_at"))

    for decision in AgentDecision.objects.all().iterator():
        previous = decision.coverage_assessment
        decision.coverage_assessment = AGENT_FORWARD.get(previous, "not_assessed")
        if previous == "skipped":
            decision.participant_control = "skip"
        elif previous == "stopped":
            decision.participant_control = "stop"
        decision.save(
            update_fields=["coverage_assessment", "participant_control"]
        )

    for item in StructuredDigestItem.objects.all().iterator():
        previous = item.coverage_status
        item.coverage_status = DIGEST_FORWARD.get(previous, "not_assessed")
        item.topic_reached = previous != "not_reached"
        if previous == "skipped":
            item.participant_control = "skip"
        elif previous == "stopped":
            item.participant_control = "stop"
        item.save(
            update_fields=[
                "coverage_status",
                "participant_control",
                "topic_reached",
            ]
        )


def backwards(apps, schema_editor):
    AgentDecision = apps.get_model("interviews", "AgentDecision")
    StructuredDigestItem = apps.get_model("interviews", "StructuredDigestItem")

    for decision in AgentDecision.objects.all().iterator():
        if decision.participant_control == "skip":
            previous = "skipped"
        elif decision.participant_control == "stop":
            previous = "stopped"
        else:
            previous = {
                "covered": "sufficient",
                "partially_covered": "partial",
                "unclear": "vague",
                "off_topic": "off_topic",
                "not_assessed": "safety_boundary",
            }.get(decision.coverage_assessment, "vague")
        decision.coverage_assessment = previous
        decision.save(update_fields=["coverage_assessment"])

    for item in StructuredDigestItem.objects.all().iterator():
        if item.participant_control == "skip":
            previous = "skipped"
        elif item.participant_control == "stop":
            previous = "stopped"
        elif not item.topic_reached:
            previous = "not_reached"
        else:
            previous = {
                "covered": "answered",
                "partially_covered": "partial",
                "not_covered": "partial",
                "not_assessed": "partial",
            }.get(item.coverage_status, "partial")
        item.coverage_status = previous
        item.save(update_fields=["coverage_status"])


class Migration(migrations.Migration):

    dependencies = [
        ("interviews", "0009_rename_final_review_checks"),
    ]

    operations = [
        migrations.AddField(
            model_name="interviewsession",
            name="consent_confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="interviewsession",
            name="consent_notice_version",
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name="interviewsession",
            name="consent_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="interviewsession",
            name="consent_snapshot_sha256",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.RenameField(
            model_name="agentdecision",
            old_name="answer_status",
            new_name="coverage_assessment",
        ),
        migrations.AddField(
            model_name="agentdecision",
            name="participant_control",
            field=models.CharField(
                choices=[("none", "None"), ("skip", "Skip"), ("stop", "Stop")],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="agentdecision",
            name="coverage_assessment",
            field=models.CharField(
                choices=[
                    ("covered", "Covered"),
                    ("partially_covered", "Partially covered"),
                    ("unclear", "Unclear"),
                    ("off_topic", "Off topic"),
                    ("not_assessed", "Not assessed"),
                ],
                default="not_assessed",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="structureddigestitem",
            name="participant_control",
            field=models.CharField(
                choices=[("none", "None"), ("skip", "Skip"), ("stop", "Stop")],
                default="none",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="structureddigestitem",
            name="topic_reached",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="structureddigestitem",
            name="coverage_status",
            field=models.CharField(
                choices=[
                    ("covered", "Covered"),
                    ("partially_covered", "Partially covered"),
                    ("not_covered", "Not covered"),
                    ("not_assessed", "Not assessed"),
                ],
                default="not_assessed",
                max_length=30,
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
