# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the TUI's text editing flow.

Every text the TUI edits — instructions, ``project.yml``, ``config.yml``
and Docker snippets — goes through one flow in
[`ProjectActionsMixin`][terok.tui.project_actions.ProjectActionsMixin]:
``$EDITOR`` on a draft file where the operator's settings send edits
there, the integrated editor otherwise, and the text's validator before
anything is written.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from terok.lib.core.instructions import inherits_instructions
from terok.tui.project_actions import (
    EditableText,
    InheritCheckbox,
    ProjectActionsMixin,
    _snippet_texts,
)
from terok.tui.text_screens import MARKDOWN, EditedText
from tests.testfs import MOCK_BASE


def _instance(*, is_web: bool = False) -> mock.Mock:
    """A mixin stand-in with the collaborators the editing flow touches."""
    instance = mock.Mock(spec=ProjectActionsMixin)
    instance.is_web = is_web
    instance.notify = mock.Mock()
    instance.push_screen = mock.Mock()
    instance.push_screen_wait = mock.AsyncMock()
    instance._run_suspended = mock.AsyncMock(return_value=0)
    instance._edit_in_external_editor = mock.AsyncMock()
    instance._external_editor = mock.Mock(return_value=None)
    instance._available_editor = mock.Mock(return_value=None)
    # The real steps between them, so a test drives the composed flow.
    instance._edit_once = partial(ProjectActionsMixin._edit_once, instance)
    instance._external_edit_hook = partial(ProjectActionsMixin._external_edit_hook, instance)
    # Provided by TerokTUI, so absent from the mixin's spec.
    instance.refresh_projects = mock.AsyncMock()
    instance._refresh_project_state = mock.Mock()
    instance._offer_restart_for_config = mock.Mock()
    return instance


def _text(
    *,
    load: str = "body",
    save: mock.Mock | None = None,
    validate: object = None,
    language: str | None = None,
    inherit: InheritCheckbox | None = None,
) -> EditableText:
    """An in-memory editable text."""
    return EditableText(
        title="T",
        draft_name="draft.yml",
        load=lambda: load,
        save=save or mock.Mock(),
        validate=validate,  # type: ignore[arg-type]
        language=language,
        inherit=inherit,
    )


def _fake_editor(*edits: str) -> mock.AsyncMock:
    """A ``_run_suspended`` stand-in that edits the draft: each run writes the next text."""
    texts = iter(edits)

    async def edit(*argv: str, prompt_on_success: bool) -> int:
        Path(argv[-1]).write_text(next(texts), encoding="utf-8")
        return 0

    return mock.AsyncMock(side_effect=edit)


# ── Which editor ──────────────────────────────────────────────────────


class TestExternalEditorChoice:
    """``$EDITOR`` takes the edit only in a local terminal, when set and preferred."""

    def _choice(self, *, is_web: bool, environ: dict[str, str], preferred: bool) -> str | None:
        instance = _instance(is_web=is_web)
        instance._available_editor = partial(ProjectActionsMixin._available_editor, instance)
        with (
            mock.patch.dict("terok.tui.project_actions.os.environ", environ, clear=True),
            mock.patch(
                "terok.lib.api.get_config", return_value=mock.Mock(tui_external_editor=preferred)
            ),
        ):
            return ProjectActionsMixin._external_editor(instance)

    def test_local_terminal_with_editor_and_preference(self) -> None:
        assert self._choice(is_web=False, environ={"EDITOR": "vim"}, preferred=True) == "vim"

    def test_web_tui_never_uses_editor(self) -> None:
        """The web TUI has no terminal for ``$EDITOR`` to take over."""
        assert self._choice(is_web=True, environ={"EDITOR": "vim"}, preferred=True) is None

    def test_preference_off_keeps_the_integrated_editor(self) -> None:
        assert self._choice(is_web=False, environ={"EDITOR": "vim"}, preferred=False) is None

    def test_no_editor_set_keeps_the_integrated_editor(self) -> None:
        assert self._choice(is_web=False, environ={}, preferred=True) is None


class TestAvailableEditor:
    """The button needs a set ``$EDITOR`` and a terminal, whatever the preference says."""

    def _available(self, *, is_web: bool, environ: dict[str, str]) -> str | None:
        instance = _instance(is_web=is_web)
        with mock.patch.dict("terok.tui.project_actions.os.environ", environ, clear=True):
            return ProjectActionsMixin._available_editor(instance)

    def test_set_editor_is_available_with_the_preference_off(self) -> None:
        assert self._available(is_web=False, environ={"EDITOR": "vim"}) == "vim"

    @pytest.mark.parametrize("environ", [{}, {"EDITOR": ""}, {"EDITOR": "  "}], ids=str)
    def test_unset_or_blank_editor_is_not_available(self, environ: dict[str, str]) -> None:
        assert self._available(is_web=False, environ=environ) is None

    def test_web_tui_has_none(self) -> None:
        assert self._available(is_web=True, environ={"EDITOR": "vim"}) is None

    async def test_integrated_editor_gets_the_round_trip(self) -> None:
        instance = _instance()
        instance._available_editor.return_value = "vim"
        instance.push_screen_wait.return_value = None
        with mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls:
            await ProjectActionsMixin._edit_and_save(
                instance, _text(), done_msg="ok", after_save=None
            )
        hook = screen_cls.call_args.kwargs["edit_externally"]
        await hook("draft")
        instance._edit_in_external_editor.assert_awaited_once_with(
            "vim", "draft", draft_name="draft.yml", title="T", validate=None
        )


# ── Edit and save ─────────────────────────────────────────────────────


class TestEditAndSave:
    """``_edit_and_save`` edits a text and saves an accepted change."""

    async def test_integrated_editor_gets_the_text_language_and_validator(self) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = None
        validator = mock.Mock(return_value=None)
        text = _text(validate=validator, language="yaml")
        with mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls:
            await ProjectActionsMixin._edit_and_save(instance, text, done_msg="ok", after_save=None)
        screen_cls.assert_called_once_with(
            "body",
            title="T",
            language="yaml",
            validate=validator,
            reference=None,
            edit_externally=None,
            checkbox_label=None,
            checked=False,
        )
        instance.push_screen_wait.assert_awaited_once_with(screen_cls.return_value)

    async def test_changed_text_is_saved_then_reported_and_followed_up(self) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = EditedText("new body")
        save, after_save = mock.Mock(), mock.AsyncMock()
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save), done_msg="saved!", after_save=after_save
        )
        save.assert_called_once_with("new body")
        instance.notify.assert_called_once_with("saved!", markup=False)
        after_save.assert_awaited_once_with()

    async def test_plain_function_runs_after_save(self) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = EditedText("new body")
        after_save = mock.Mock(return_value=None)
        await ProjectActionsMixin._edit_and_save(
            instance, _text(), done_msg="saved!", after_save=after_save
        )
        after_save.assert_called_once_with()

    async def test_cancelled_edit_writes_nothing_and_says_nothing(self) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = None
        save, after_save = mock.Mock(), mock.Mock()
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save), done_msg="saved!", after_save=after_save
        )
        save.assert_not_called()
        instance.notify.assert_not_called()
        after_save.assert_not_called()

    async def test_unchanged_text_is_not_written_and_says_so(self) -> None:
        """A forking GUI editor returns at once; the notice shows that nothing was saved."""
        instance = _instance()
        instance.push_screen_wait.return_value = EditedText("body")
        save, after_save = mock.Mock(), mock.Mock()
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save), done_msg="saved!", after_save=after_save
        )
        save.assert_not_called()
        instance.notify.assert_called_once_with("No changes to save.")
        after_save.assert_not_called()

    async def test_external_editor_result_is_saved(self) -> None:
        instance = _instance()
        instance._external_editor.return_value = "vim"
        instance._edit_in_external_editor.return_value = "edited"
        validator = mock.Mock(return_value=None)
        save = mock.Mock()
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save, validate=validator), done_msg="ok", after_save=None
        )
        instance._edit_in_external_editor.assert_awaited_once_with(
            "vim", "body", draft_name="draft.yml", title="T", validate=validator
        )
        instance.push_screen_wait.assert_not_awaited()
        save.assert_called_once_with("edited")

    async def test_unreadable_text_is_reported(self) -> None:
        instance = _instance()

        def _unreadable() -> str:
            raise PermissionError("denied")

        text = EditableText(title="T", draft_name="d", load=_unreadable, save=mock.Mock())
        await ProjectActionsMixin._edit_and_save(instance, text, done_msg="ok", after_save=None)
        instance.push_screen_wait.assert_not_awaited()
        assert "Cannot read T" in instance.notify.call_args.args[0]

    @pytest.mark.parametrize("error", [OSError("read-only"), SystemExit("project gone")])
    async def test_failed_write_reopens_the_editor_with_the_edit(
        self, error: BaseException
    ) -> None:
        """A write that fails keeps the edit: the integrated editor opens again on it."""
        instance = _instance()
        instance.push_screen_wait.side_effect = [EditedText("new body"), None]
        after_save = mock.Mock()
        save = mock.Mock(side_effect=error)
        with mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls:
            await ProjectActionsMixin._edit_and_save(
                instance, _text(save=save), done_msg="ok", after_save=after_save
            )
        assert [c.args[0] for c in screen_cls.call_args_list] == ["body", "new body"]
        assert "Cannot save T" in instance.notify.call_args.args[0]
        assert instance.notify.call_args.kwargs == {"severity": "error", "markup": False}
        after_save.assert_not_called()

    async def test_integrated_editor_shows_the_inherit_setting(self, tmp_path: Path) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = None
        text = _text(inherit=InheritCheckbox("Include", tmp_path / "project.yml"))
        with mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls:
            await ProjectActionsMixin._edit_and_save(instance, text, done_msg="ok", after_save=None)
        assert screen_cls.call_args.kwargs["checkbox_label"] == "Include"
        assert screen_cls.call_args.kwargs["checked"] is True

    async def test_changed_setting_alone_is_saved(self, tmp_path: Path) -> None:
        """Only the checkbox changed: that is a change to save, and the text stays unwritten."""
        project_yml = tmp_path / "project.yml"
        instance = _instance()
        instance.push_screen_wait.return_value = EditedText("body", checked=False)
        save = mock.Mock()
        await ProjectActionsMixin._edit_and_save(
            instance,
            _text(save=save, inherit=InheritCheckbox("Include", project_yml)),
            done_msg="saved!",
            after_save=None,
        )
        save.assert_not_called()
        instance.notify.assert_called_once_with("saved!", markup=False)
        assert inherits_instructions(project_yml) is False

    async def test_unchanged_setting_is_not_written(self, tmp_path: Path) -> None:
        project_yml = tmp_path / "project.yml"
        instance = _instance()
        instance.push_screen_wait.return_value = EditedText("body", checked=True)
        await ProjectActionsMixin._edit_and_save(
            instance,
            _text(inherit=InheritCheckbox("Include", project_yml)),
            done_msg="ok",
            after_save=None,
        )
        instance.notify.assert_called_once_with("No changes to save.")
        assert not project_yml.exists()

    async def test_setting_that_cannot_be_switched_shows_no_checkbox(self, tmp_path: Path) -> None:
        project_yml = tmp_path / "project.yml"
        project_yml.write_text("agent:\n  instructions: flat text\n", encoding="utf-8")
        instance = _instance()
        instance.push_screen_wait.return_value = None
        text = _text(inherit=InheritCheckbox("Include", project_yml))
        with mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls:
            await ProjectActionsMixin._edit_and_save(instance, text, done_msg="ok", after_save=None)
        assert screen_cls.call_args.kwargs["checkbox_label"] is None

    async def test_refused_setting_keeps_the_saved_text_and_the_choice(
        self, tmp_path: Path
    ) -> None:
        """The text saved and the setting did not: the editor reopens without saving it twice."""
        project_yml = tmp_path / "project.yml"
        instance = _instance()
        instance.push_screen_wait.side_effect = [EditedText("new", checked=False), None]
        save = mock.Mock()
        with (
            mock.patch("terok.tui.text_screens.TextEditorScreen") as screen_cls,
            mock.patch(
                "terok.lib.api.agents.set_instructions_inherit", side_effect=OSError("read-only")
            ),
        ):
            await ProjectActionsMixin._edit_and_save(
                instance,
                _text(save=save, inherit=InheritCheckbox("Include", project_yml)),
                done_msg="ok",
                after_save=None,
            )
        save.assert_called_once_with("new")
        assert screen_cls.call_args.args[0] == "new"
        assert screen_cls.call_args.kwargs["checked"] is False
        assert "Cannot save T" in instance.notify.call_args.args[0]

    async def test_external_editor_leaves_the_setting_alone(self, tmp_path: Path) -> None:
        project_yml = tmp_path / "project.yml"
        instance = _instance()
        instance._external_editor.return_value = "vim"
        instance._edit_in_external_editor.return_value = "new body"
        save = mock.Mock()
        await ProjectActionsMixin._edit_and_save(
            instance,
            _text(save=save, inherit=InheritCheckbox("Include", project_yml)),
            done_msg="ok",
            after_save=None,
        )
        save.assert_called_once_with("new body")
        assert not project_yml.exists()

    async def test_failed_write_with_editor_offers_discard_or_edit_again(self) -> None:
        """With ``$EDITOR``, the reason shows in the discard dialog; Cancel edits again."""
        instance = _instance()
        instance._external_editor.return_value = "vim"
        instance._edit_in_external_editor.side_effect = ["first edit", "second edit"]
        instance._confirm_discard = mock.AsyncMock(return_value=False)
        save = mock.Mock(side_effect=[OSError("read-only"), None])
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save), done_msg="ok", after_save=None
        )
        title, reason = instance._confirm_discard.await_args.args
        assert "Cannot save T: read-only" in reason
        second_draft = instance._edit_in_external_editor.await_args_list[1].args[1]
        assert second_draft == "first edit"
        save.assert_called_with("second edit")
        instance.notify.assert_called_with("ok", markup=False)

    async def test_failed_write_with_editor_can_be_discarded(self) -> None:
        instance = _instance()
        instance._external_editor.return_value = "vim"
        instance._edit_in_external_editor.return_value = "edit"
        instance._confirm_discard = mock.AsyncMock(return_value=True)
        save = mock.Mock(side_effect=OSError("read-only"))
        await ProjectActionsMixin._edit_and_save(
            instance, _text(save=save), done_msg="ok", after_save=None
        )
        assert instance._edit_in_external_editor.await_count == 1
        instance.notify.assert_not_called()

    async def test_worker_turns_a_failure_into_a_notification(self) -> None:
        """An unexpected error in the flow must not take the TUI down."""
        instance = _instance()
        instance._edit_and_save = mock.AsyncMock(side_effect=RuntimeError("boom"))
        await ProjectActionsMixin._run_text_edit.__wrapped__(instance, _text(), "ok", None)
        message = instance.notify.call_args.args[0]
        assert "Editing T failed" in message
        assert "boom" in message


# ── $EDITOR on a draft ────────────────────────────────────────────────


class TestEditInExternalEditor:
    """``_edit_in_external_editor`` edits a draft file and validates it on editor exit."""

    async def _edit(self, instance: mock.Mock, validate: object = None) -> str | None:
        return await ProjectActionsMixin._edit_in_external_editor(
            instance,
            "vim -u NONE",
            "original",
            draft_name="project.yml",
            title="T",
            validate=validate,  # type: ignore[arg-type]
        )

    async def test_editor_edits_a_private_draft_named_like_the_file(self) -> None:
        instance = _instance()
        instance._run_suspended = _fake_editor("edited")
        assert await self._edit(instance) == "edited"
        argv = instance._run_suspended.await_args.args
        assert argv[:3] == ("vim", "-u", "NONE")
        draft = Path(argv[3])
        assert draft.name == "project.yml"
        assert draft.parent.name.startswith("terok-edit-")
        assert not draft.parent.exists()
        assert instance._run_suspended.await_args.kwargs == {"prompt_on_success": False}

    async def test_draft_starts_with_the_current_text(self) -> None:
        instance = _instance()
        seen: list[str] = []

        async def _read_draft(*argv: str, prompt_on_success: bool) -> int:
            seen.append(Path(argv[-1]).read_text(encoding="utf-8"))
            return 0

        instance._run_suspended = mock.AsyncMock(side_effect=_read_draft)
        await self._edit(instance)
        assert seen == ["original"]

    @pytest.mark.parametrize("code", [1, None], ids=["non-zero-exit", "launch-failure"])
    async def test_failed_or_aborted_editor_abandons_the_edit(self, code: int | None) -> None:
        instance = _instance()
        instance._run_suspended = mock.AsyncMock(return_value=code)
        assert await self._edit(instance) is None

    async def test_refused_text_goes_back_to_the_editor(self) -> None:
        """A refused draft is shown with its reason, then edited again until it passes."""
        instance = _instance()
        instance._run_suspended = _fake_editor("bad", "good")
        instance._confirm_discard = mock.AsyncMock(return_value=False)
        validator = mock.Mock(side_effect=lambda text: "broken" if text == "bad" else None)
        assert await self._edit(instance, validator) == "good"
        instance._confirm_discard.assert_awaited_once_with("T", "broken")
        assert instance._run_suspended.await_count == 2

    async def test_unchanged_draft_comes_back_without_a_check(self) -> None:
        """Quitting without a change is no edit — even for a text that does not load."""
        instance = _instance()
        instance._run_suspended = _fake_editor("original")
        instance._confirm_discard = mock.AsyncMock()
        validator = mock.Mock(return_value="broken")
        assert await self._edit(instance, validator) == "original"
        validator.assert_not_called()
        instance._confirm_discard.assert_not_awaited()

    async def test_draft_that_is_not_utf8_goes_back_to_the_editor(self) -> None:
        """An editor saving another encoding loses nothing: the draft opens again."""
        instance = _instance()
        runs = iter([b"caf\xe9\n", "café\n".encode()])

        async def _edit_bytes(*argv: str, prompt_on_success: bool) -> int:
            Path(argv[-1]).write_bytes(next(runs))
            return 0

        instance._run_suspended = mock.AsyncMock(side_effect=_edit_bytes)
        instance._confirm_discard = mock.AsyncMock(return_value=False)
        assert await self._edit(instance) == "café\n"
        _title, verdict = instance._confirm_discard.await_args.args
        assert "not valid UTF-8" in verdict

    async def test_discarding_a_refused_text_abandons_the_edit(self) -> None:
        instance = _instance()
        instance._run_suspended = _fake_editor("bad")
        instance._confirm_discard = mock.AsyncMock(return_value=True)
        assert await self._edit(instance, mock.Mock(return_value="broken")) is None
        assert instance._run_suspended.await_count == 1

    async def test_discard_prompt_offers_discard_and_defaults_to_editing_again(self) -> None:
        instance = _instance()
        instance.push_screen_wait.return_value = False
        with mock.patch("terok.tui.screens.ConfirmDestructiveScreen") as confirm_cls:
            discarded = await ProjectActionsMixin._confirm_discard(instance, "T", "broken")
        assert discarded is False
        kwargs = confirm_cls.call_args.kwargs
        assert kwargs["confirm_label"] == "Discard"
        assert kwargs["message"].startswith("broken")
        assert "T" in kwargs["title"]


# ── The texts ─────────────────────────────────────────────────────────


class TestEditableTextFromFile:
    """``EditableText.from_file`` reads and writes one file."""

    def test_missing_file_reads_empty_and_save_creates_it(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "instructions.md"
        text = EditableText.from_file(path, title="T")
        assert text.load() == ""
        assert text.draft_name == "instructions.md"
        text.save("new body")
        assert path.read_text(encoding="utf-8") == "new body"
        assert text.load() == "new body"
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700

    def test_save_keeps_an_existing_file_mode_and_link(self, tmp_path: Path) -> None:
        target = tmp_path / "dotfiles" / "project.yml"
        target.parent.mkdir()
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o644)
        link = tmp_path / "project.yml"
        link.symlink_to(target)
        EditableText.from_file(link, title="T").save("new\n")
        assert link.is_symlink()
        assert target.read_text(encoding="utf-8") == "new\n"
        assert target.stat().st_mode & 0o777 == 0o644


class TestSnippetTexts:
    """``_snippet_texts`` finds a project's Docker snippets where ``project.yml`` puts them."""

    def _project(self, *, snippet_path: Path | None, inline: str | None) -> SimpleNamespace:
        return SimpleNamespace(name="demo", snippet_path=snippet_path, snippet_inline=inline)

    def test_file_only(self, tmp_path: Path) -> None:
        path = tmp_path / "extra.dockerfile"
        path.write_text("RUN one\n", encoding="utf-8")
        (snippet,) = _snippet_texts(self._project(snippet_path=path, inline=None))  # type: ignore[arg-type]
        assert snippet.load() == "RUN one\n"
        assert snippet.draft_name == "extra.dockerfile"

    def test_blank_inline_beside_a_file_is_not_offered(self, tmp_path: Path) -> None:
        """The build skips a blank inline snippet, so the editor does too."""
        project = self._project(snippet_path=tmp_path / "extra.dockerfile", inline="  \n")
        assert len(_snippet_texts(project)) == 1  # type: ignore[arg-type]

    def test_file_and_inline_both_offered_file_first(self, tmp_path: Path) -> None:
        project = self._project(snippet_path=tmp_path / "extra.dockerfile", inline="RUN two\n")
        file_snippet, inline_snippet = _snippet_texts(project)  # type: ignore[arg-type]
        assert "extra.dockerfile" in file_snippet.title
        assert inline_snippet.load() == "RUN two\n"

    @pytest.mark.parametrize("inline", [None, "RUN two\n"], ids=["no-snippet", "inline"])
    def test_inline_snippet_saves_into_project_yml(self, inline: str | None) -> None:
        """Without a snippet file, the inline snippet is the one to edit — or to start."""
        with mock.patch("terok.lib.api.set_project_snippet_inline") as write:
            (snippet,) = _snippet_texts(self._project(snippet_path=None, inline=inline))  # type: ignore[arg-type]
            snippet.save("RUN three\n")
        assert snippet.load() == (inline or "")
        assert snippet.draft_name == "snippet.dockerfile"
        write.assert_called_once_with("demo", "RUN three\n")


# ── The edit actions ──────────────────────────────────────────────────


class TestEditActions:
    """Each edit action hands the right text and follow-up to ``_edit_text``."""

    def _edited(self, instance: mock.Mock) -> tuple[EditableText, dict[str, object]]:
        """The text and keyword arguments of the single ``_edit_text`` call."""
        instance._edit_text.assert_called_once()
        (text,), kwargs = instance._edit_text.call_args
        return text, kwargs

    def test_project_yml_is_validated_yaml_and_rescans_projects(self, tmp_path: Path) -> None:
        instance = _instance()
        path = tmp_path / "demo" / "project.yml"
        with (
            mock.patch("terok.lib.api.project_config_path", return_value=path),
            mock.patch("terok.lib.api.validate_project_yaml") as validate,
        ):
            ProjectActionsMixin._edit_project_yml(instance, "demo")
            text, kwargs = self._edited(instance)
            text.validate("draft")  # type: ignore[misc]
        validate.assert_called_once_with("demo", "draft")
        assert text.language == "yaml"
        assert text.draft_name == "project.yml"
        assert kwargs["after_save"] is instance.refresh_projects

    def test_project_yml_of_an_unknown_project_is_reported(self) -> None:
        instance = _instance()
        with mock.patch("terok.lib.api.project_config_path", side_effect=SystemExit("not found")):
            ProjectActionsMixin._edit_project_yml(instance, "nope")
        instance._edit_text.assert_not_called()
        instance.notify.assert_called_once_with("not found", severity="error", markup=False)

    async def test_project_yml_action_edits_the_current_project(self) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        await ProjectActionsMixin._action_edit_project_yml(instance)
        instance._edit_project_yml.assert_called_once_with("demo")

    async def test_global_config_is_validated_and_offers_a_restart(self, tmp_path: Path) -> None:
        instance = _instance()
        path = tmp_path / "config.yml"
        with (
            mock.patch("terok.lib.api.get_config", return_value=mock.Mock(global_config_path=path)),
            mock.patch("terok.lib.api.validate_global_config") as validate,
        ):
            await ProjectActionsMixin._action_edit_global_config(instance)
            text, kwargs = self._edited(instance)
        assert text.validate is validate
        assert text.language == "yaml"
        assert text.draft_name == "config.yml"
        assert kwargs["after_save"] is instance._offer_restart_for_config

    async def test_instructions_refresh_the_project_state(self, tmp_path: Path) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        project = SimpleNamespace(root=tmp_path)
        with mock.patch("terok.tui.project_actions.load_project", return_value=project):
            await ProjectActionsMixin._action_edit_instructions(instance)
        text, kwargs = self._edited(instance)
        assert text.draft_name == "instructions.md"
        assert text.validate is None
        assert kwargs == {
            "done_msg": "Instructions updated for demo",
            "after_save": instance._refresh_project_state,
        }

    async def test_global_instructions_sit_next_to_the_config_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "config.yml"))
        instance = _instance()
        with mock.patch(
            "terok.lib.api.get_config",
            return_value=mock.Mock(global_config_path=tmp_path / "config.yml"),
        ):
            await ProjectActionsMixin._action_edit_global_instructions(instance)
        text, kwargs = self._edited(instance)
        (tmp_path / "instructions.md").write_text("global", encoding="utf-8")
        assert text.load() == "global"
        assert kwargs["done_msg"] == "Global instructions updated"

    async def test_global_instructions_are_markdown_with_the_global_setting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_yml = tmp_path / "config.yml"
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_yml))
        instance = _instance()
        with mock.patch(
            "terok.lib.api.get_config", return_value=mock.Mock(global_config_path=config_yml)
        ):
            await ProjectActionsMixin._action_edit_global_instructions(instance)
        text, _ = self._edited(instance)
        assert text.language == MARKDOWN
        assert text.inherit == InheritCheckbox(
            "Include the bundled default instructions", config_yml
        )

    async def test_project_instructions_are_markdown_with_the_project_setting(
        self, tmp_path: Path
    ) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        with mock.patch(
            "terok.tui.project_actions.load_project", return_value=mock.Mock(root=tmp_path)
        ):
            await ProjectActionsMixin._action_edit_instructions(instance)
        text, _ = self._edited(instance)
        assert text.language == MARKDOWN
        assert text.inherit is not None
        assert text.inherit.config_file == tmp_path / "project.yml"

    async def test_yaml_configs_carry_their_options_reference(self, tmp_path: Path) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        with (
            mock.patch("terok.lib.api.project_config_path", return_value=tmp_path / "project.yml"),
            mock.patch(
                "terok.lib.api.get_config",
                return_value=mock.Mock(global_config_path=tmp_path / "config.yml"),
            ),
        ):
            ProjectActionsMixin._edit_project_yml(instance, "demo")
            project_text = instance._edit_text.call_args.args[0]
            await ProjectActionsMixin._action_edit_global_config(instance)
            global_text = instance._edit_text.call_args.args[0]
        assert "\n## default_agent\n" in project_text.reference()
        assert "\n## external_editor\n" in global_text.reference()

    async def test_toggle_flips_the_project_setting(self, tmp_path: Path) -> None:
        project_yml = tmp_path / "project.yml"
        project_yml.write_text("agent:\n  instructions: [_inherit, extra]\n", encoding="utf-8")
        instance = _instance()
        instance.current_project_name = "demo"
        with mock.patch("terok.lib.api.project_config_path", return_value=project_yml):
            await ProjectActionsMixin._action_toggle_instructions_inherit(instance)
            assert inherits_instructions(project_yml) is False
            await ProjectActionsMixin._action_toggle_instructions_inherit(instance)
            assert inherits_instructions(project_yml) is True
        assert "extra" in project_yml.read_text(encoding="utf-8")
        instance._refresh_project_state.assert_called()

    async def test_toggle_refuses_a_form_it_cannot_switch(self, tmp_path: Path) -> None:
        project_yml = tmp_path / "project.yml"
        project_yml.write_text("agent:\n  instructions: flat text\n", encoding="utf-8")
        instance = _instance()
        instance.current_project_name = "demo"
        with mock.patch("terok.lib.api.project_config_path", return_value=project_yml):
            await ProjectActionsMixin._action_toggle_instructions_inherit(instance)
        assert instance.notify.call_args.kwargs == {"severity": "warning"}
        assert "flat text" in project_yml.read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("choice", "action"),
        [
            ("edit_global", "_action_edit_global_instructions"),
            ("show_default", "_action_show_default_instructions"),
        ],
    )
    async def test_instructions_screen_choice_runs_its_action(
        self, choice: str, action: str
    ) -> None:
        instance = _instance()
        setattr(instance, action, mock.AsyncMock())
        await ProjectActionsMixin._on_instructions_choice(instance, choice)
        getattr(instance, action).assert_awaited_once_with()

    async def test_closed_instructions_screen_runs_nothing(self) -> None:
        instance = _instance()
        instance._action_edit_global_instructions = mock.AsyncMock()
        instance._action_show_default_instructions = mock.AsyncMock()
        await ProjectActionsMixin._on_instructions_choice(instance, None)
        instance._action_edit_global_instructions.assert_not_awaited()
        instance._action_show_default_instructions.assert_not_awaited()

    @pytest.mark.parametrize(
        "action",
        ["_action_edit_snippet", "_action_edit_instructions"],
        ids=["snippet", "instructions"],
    )
    async def test_project_that_no_longer_loads_is_reported(self, action: str) -> None:
        """A project.yml broken since the last scan must not take the TUI down."""
        instance = _instance()
        instance.current_project_name = "demo"
        with mock.patch(
            "terok.tui.project_actions.load_project", side_effect=SystemExit("Failed to read")
        ):
            await getattr(ProjectActionsMixin, action)(instance)
        instance.notify.assert_called_once_with("Failed to read", severity="error", markup=False)
        instance._edit_text.assert_not_called()
        instance._edit_snippet.assert_not_called()

    async def test_single_snippet_opens_directly(self) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        project = SimpleNamespace(name="demo", snippet_path=None, snippet_inline=None)
        with mock.patch("terok.tui.project_actions.load_project", return_value=project):
            await ProjectActionsMixin._action_edit_snippet(instance)
        instance._edit_snippet.assert_called_once()
        instance.push_screen.assert_not_called()

    async def test_two_snippets_ask_which_one(self) -> None:
        instance = _instance()
        instance.current_project_name = "demo"
        project = SimpleNamespace(
            name="demo", snippet_path=MOCK_BASE / "extra.dockerfile", snippet_inline="RUN two\n"
        )
        with mock.patch("terok.tui.project_actions.load_project", return_value=project):
            await ProjectActionsMixin._action_edit_snippet(instance)
        chooser, on_pick = instance.push_screen.call_args.args
        assert type(chooser).__name__ == "ChoiceScreen"
        assert [choice_id for choice_id, _label in chooser._choices] == ["0", "1"]

        on_pick(None)
        instance._edit_snippet.assert_not_called()
        on_pick("1")
        (picked,) = instance._edit_snippet.call_args.args
        assert picked.load() == "RUN two\n"

    def test_snippet_save_rescans_and_reminds_about_the_rebuild(self) -> None:
        instance = _instance()
        snippet = _text()
        ProjectActionsMixin._edit_snippet(instance, snippet)
        text, kwargs = self._edited(instance)
        assert text is snippet
        assert "Rebuild the project image" in str(kwargs["done_msg"])
        assert kwargs["after_save"] is instance.refresh_projects


class TestWizardReviewScreen:
    """The wizard's review step gets the project.yml check and, where edits go there, ``$EDITOR``."""

    def _review(self, instance: mock.Mock) -> dict[str, object]:
        with mock.patch("terok.tui.wizard_screens.ProjectReviewScreen") as review_cls:
            ProjectActionsMixin._project_review_screen(instance, "fresh", "rendered")
        args, kwargs = review_cls.call_args
        assert args == ("fresh", "rendered")
        return kwargs

    def test_validates_the_text_as_the_new_project(self) -> None:
        from terok.lib.api import validate_project_yaml

        kwargs = self._review(_instance())
        validate = kwargs["validate"]
        assert isinstance(validate, partial)
        assert validate.func is validate_project_yaml
        assert validate.args == ("fresh",)
        assert kwargs["edit_externally"] is None

    async def test_offers_the_editor_round_trip_where_edits_go_there(self) -> None:
        instance = _instance()
        instance._available_editor.return_value = "vim"
        kwargs = self._review(instance)
        edit_externally = kwargs["edit_externally"]
        await edit_externally("draft")  # type: ignore[operator]
        instance._edit_in_external_editor.assert_awaited_once_with(
            "vim",
            "draft",
            draft_name="project.yml",
            title="project.yml — fresh",
            validate=kwargs["validate"],
        )


class TestExternalEditHook:
    """``_external_edit_hook`` gives a text its ``$EDITOR`` round-trip, where one can run."""

    def test_no_hook_where_edits_stay_in_the_tui(self) -> None:
        hook = ProjectActionsMixin._external_edit_hook(
            _instance(), draft_name="snippet.dockerfile", title="Docker snippet"
        )
        assert hook is None

    async def test_hook_edits_a_named_draft_without_a_check(self) -> None:
        instance = _instance()
        instance._available_editor.return_value = "vim"
        hook = ProjectActionsMixin._external_edit_hook(
            instance, draft_name="snippet.dockerfile", title="Docker snippet"
        )
        assert hook is not None
        await hook("RUN one\n")
        instance._edit_in_external_editor.assert_awaited_once_with(
            "vim",
            "RUN one\n",
            draft_name="snippet.dockerfile",
            title="Docker snippet",
            validate=None,
        )
