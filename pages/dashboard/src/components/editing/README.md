# Editing components

This module supplies the reusable, controlled editing surface for Memora Dashboard records. It keeps generic view, edit, create, validation, tag, conflict, and destructive-confirmation behavior in one place so domain pages only own their drafts and backend requests.

## Public component inventory

| Component | Purpose |
| --- | --- |
| `EntityEditorSheet` | Controlled right-side detail Sheet with view and edit modes. |
| `EntityCreateDialog` | Controlled create Dialog with the same fixed header, scrollable form, and fixed action area. |
| `EditFormLayout` | Form-level validation summary plus registered invalid-control focus and field-error IDs. |
| `UnsavedChangesDialog` | Confirms an explicit keep-editing or discard choice. |
| `EditConflictDialog` | Offers explicit load-remote and reapply-local callbacks; it never saves. |
| `DeleteConfirmDialog` | Accessible destructive confirmation with optional exact-text acknowledgement. |
| `TagEditor` | Controlled tag list with a separate pending input, keyboard entry, removal, duplicate filtering, and a count cap. |

## Usage rules

- All surfaces are controlled. The caller owns `open`, draft values, dirty state, submission state, validation state, and the actual data request.
- `EntityEditorSheet` starts in the caller-selected `view` or `edit` mode. Its close request is sent through `onOpenChange(false)` so the owning page can open `UnsavedChangesDialog` when the draft is dirty.
- A caller must set `isSubmitting` while its request is outstanding. The Sheet and Dialog disable action and close controls in that state; the components also suppress an immediate duplicate invocation. If a save or submit callback throws or rejects, the component consumes that transport-level failure, resets its internal duplicate guard, and leaves error presentation to the caller-owned state.
- A save shortcut is deliberately narrow: `Ctrl+Enter` and `Meta+Enter` submit only a dirty, valid, idle form. There is no delete keyboard shortcut.
- Use `EditFormLayout` around domain inputs after a failed validation attempt. Pass each input to `registerField(name, element)` and attach `getFieldError(name)?.id` to that input's `aria-describedby`. Set `focusInvalid` for the failed submission render; it focuses only when validation becomes active, the first invalid field changes, or form-only errors replace field errors, rather than stealing focus on equivalent rerenders.
- Use `DeleteConfirmDialog.confirmationRequirement` for cross-group or large-batch deletion. Ordinary single-record deletion omits it and remains a one-step explicit destructive action.
- `TagEditor` requires `getRemoveLabel(tag)` to provide the translated accessible name for each remove button. It treats values as exact strings after trimming the pending input, ignores new empty and duplicate entries, preserves any duplicate values the caller already supplies, removes exactly the selected tag occurrence, removes the last tag with Backspace only while the input is empty, and calls `onLimitReached` when its configured limit is reached.

## Translated-label contract

These components do not look up domain i18n keys. Every visible title, description, action, field label, validation-summary label, and confirmation phrase is supplied by the caller. Base primitives retain their existing common close accessibility translation. This makes components reusable across records and keeps locale ownership with each page.

## Accessibility and responsive behavior

- Every Sheet and Dialog composes the existing Base UI primitive with a `Title` and `Description`, giving it an accessible name and description.
- Error summaries use a focusable `role="alert"`; field error IDs can be referenced by form controls through `aria-describedby`.
- Tags use a caller-provided translated input label and remove-button label builder, plus standard Enter/Backspace behavior.
- Editor surfaces divide into fixed headers, `min-h-0 flex-1 overflow-y-auto` form content, and border-top action areas. This keeps action controls reachable on narrow screens and allows long translated text or forms to scroll.

## Domain boundary

This module contains no AstrBot bridge calls, records, pagination state, page navigation, or domain translation keys. Domain forms and pages must keep their own data shape, API request, response handling, and pagination. The shared components only render controlled editing state and invoke explicit callbacks.
