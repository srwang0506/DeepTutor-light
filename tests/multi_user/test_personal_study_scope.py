from deeptutor.services.personal_study import StudyPreferencesPatch, StudyPreferencesStore
from deeptutor.services.workspace import ContentWorkspaceService
from deeptutor.services.workspace.context import workspace_context


def test_plan_state_is_private_to_account_and_workspace(as_user):
    with as_user("alice"):
        a = ContentWorkspaceService().create_workspace("Algorithms")["workspace_id"]
        b = ContentWorkspaceService().create_workspace("Research")["workspace_id"]
        with workspace_context(a):
            StudyPreferencesStore().update(
                StudyPreferencesPatch(
                    expected_revision=0,
                    minutes=15,
                    deferral={"key": "practice:1", "action": "tomorrow"},
                ),
                "UTC",
            )
        with workspace_context(b):
            assert StudyPreferencesStore().read("UTC").minutes == 30
            assert not StudyPreferencesStore().read("UTC").deferred
        with workspace_context(""):
            assert StudyPreferencesStore().read("UTC").minutes == 30
        with workspace_context(a):
            assert StudyPreferencesStore().read("UTC").minutes == 15
    with as_user("bob"), workspace_context(""):
        assert StudyPreferencesStore().read("UTC").minutes == 30
