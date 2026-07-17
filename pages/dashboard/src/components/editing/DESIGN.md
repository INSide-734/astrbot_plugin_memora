# Editing components design

## Design goals

Provide a calm, predictable operational editing experience without coupling a reusable UI layer to one Memora entity. The module makes draft loss, validation failure, server conflict, submission, and destructive actions explicit. It does not define domain fields, call the bridge, retain entity data, or implement pagination.

## State transitions

```text
Editor Sheet
  closed -> view (owner opens a selected entity)
  view -> edit (begin edit)
  edit clean -> view (cancel resets through the owner)
  edit dirty -> save request (valid and idle only)
  save request -> view (owner accepts successful response)
  edit dirty -> close request -> UnsavedChangesDialog

Create Dialog
  closed -> create (owner opens dialog)
  create clean -> close request
  create dirty -> close request -> UnsavedChangesDialog
  create dirty -> submit request (valid and idle only)
```

The components remain controlled at every transition. A close request does not silently mutate draft state: `EntityEditorSheet` and `EntityCreateDialog` call `onOpenChange(false)`, and their owner decides whether a dirty draft opens `UnsavedChangesDialog`. Keep editing closes only that confirmation; discard is the owner's explicit reset-and-close operation.

## Conflict flow

`EditConflictDialog` deliberately exposes two callbacks and no save action:

1. **Load remote values** replaces or reloads the draft through `onLoadRemote`.
2. **Reapply local values** asks the owner to apply its current draft over the latest remote values through `onReapplyLocal`.

Both choices are explicit and data-free in this module, so a domain page can preserve revision metadata and decide how to show field-level conflicts. The dialog cannot be silently dismissed while resolving an active conflict.

## Validation and accessibility contract

`EditFormLayout` receives path-indexed `fieldErrors` plus optional form errors. It derives stable, collision-resistant HTML-safe error IDs from each full field path, renders a focusable form-level `role="alert"`, and focuses the first registered invalid field when validation first becomes active or when the first invalid field changes. Equivalent rerenders do not steal a user's current focus. When only form-level errors exist, the summary itself receives focus. Domain inputs register DOM controls and connect their field error with `aria-describedby`; the module therefore supports native inputs and existing Base UI controls without creating a parallel form primitive.

All dialogs and the Sheet render existing Base UI title and description primitives. Destructive confirmation is a named, described Dialog. Tag controls have an input label and descriptive remove button labels. Save shortcut handling is limited to Ctrl+Enter and Meta+Enter; it avoids any delete shortcut and only invokes callbacks while dirty, valid, and not submitting.

## Tag rules

The pending input is local transient UI state; `values` remains caller-controlled. Enter trims and proposes a tag. Empty strings and new exact duplicates are ignored. Existing duplicate controlled values are still rendered as separate entries and each remove button deletes only its associated index. Backspace removes the last current tag only when the input is empty. A configured maximum blocks additional additions and invokes `onLimitReached` so the caller can present localized feedback. `getRemoveLabel(tag)` is required so each remove button has a caller-owned translated accessible name; the generic module has no hard-coded action text. The component never normalizes existing caller values or sends API requests.

## Responsive layout

Editor surfaces use three flex regions: a shrink-to-content header, `min-h-0 flex-1 overflow-y-auto` content, and a shrink-to-content border-top action region. The Sheet stays within the viewport width, and the Dialog has a viewport-relative maximum height. This retains a stable action area and makes long forms usable on mobile rather than relying on a page-level scroll position.

## Decisions and trade-offs

| Decision | Reason | Trade-off |
| --- | --- | --- |
| Compose existing Base UI `Sheet` and `Dialog` | Preserves focus management, overlays, motion, semantic tokens, and project-wide accessibility behavior. | Public prop contracts follow the existing primitives instead of a new modal abstraction. |
| Keep all draft and dirty state in callers | One shared module can serve records with incompatible shapes and revision protocols. | Pages must coordinate reset, dirty confirmation, and successful-save transitions. |
| Require translated labels as props | Avoids domain i18n coupling and makes each action precise in context. | Call sites provide more labels. |
| Use exact confirmation text only for higher-impact deletes | Ordinary deletion remains fast while cross-group or large-batch operations gain an intentional checkpoint. | A caller must decide when its operation meets the higher-impact threshold. |
| Scope keyboard saving to explicit modifier Enter | Supports efficient editing without hijacking normal typing or creating a destructive shortcut. | Users still activate standard buttons for other actions. |
| Reset destructive confirmation on every close | A prior acknowledgement must not authorize a later deletion session. | Users re-enter the phrase after reopening. |
| Consume editor callback failures after resetting guards | The caller already owns error state, while the generic surface must remain usable after a throw or rejection. | The component intentionally does not render a second generic error message. |
| Preserve duplicate controlled tag entries by index | Server or legacy data may already contain duplicates; deleting one must not erase all matching strings. | Reordered duplicate values are identified by their current controlled index. |

## Change history

| Date | Change |
| --- | --- |
| 2026-07-15 | Added the first shared controlled editing components for the unified CRUD dashboard work. |
| 2026-07-15 | Required caller-provided tag remove labels and reset destructive confirmation phrases between sessions. |
| 2026-07-15 | Hardened callback failures, validation focus/ID stability, and duplicate controlled tag handling. |
