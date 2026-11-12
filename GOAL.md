# Goal: Autonomous Todo List from zero

Build a complete, polished, accessible single-page Todo List application in `app/`.
The repository initially contains **no application implementation**. The AI
Worker must author the app itself. No build tools or npm dependencies are
necessary: use HTML, CSS, and vanilla JavaScript with localStorage.

## Product requirements

1. A usable and attractive page titled **Todo Lab**.
2. Add a task with a nonempty title; blank submissions do nothing.
3. Display tasks; toggle task completion; delete a task.
4. Persist task list and completion state in localStorage across reloads.
5. Filter tasks by **All**, **Active**, and **Completed**.
6. Display a friendly empty state when appropriate.
7. Accessible input, buttons, labels, and keyboard-friendly interaction.
8. No remote backend, no accounts, no external secrets or services.

## Browser contract (required for independent reviewer tests)

The HTML must expose the following stable `data-testid` selectors:

- `todo-input`: input for new task.
- `todo-add`: button for submitting the task.
- `todo-item`: one element per currently visible task.
- `todo-toggle`: native checkbox inside each `todo-item`.
- `todo-delete`: button inside each `todo-item`.
- `filter-all`, `filter-active`, `filter-completed`: filter buttons.

Use an `h1` containing `Todo`. Keep the app served directly as `app/index.html`
by Python's built-in `http.server`. You may create supporting CSS/JS files inside
`app/`, but DO NOT alter orchestration code or configuration.

## Three required milestones

- `SCAFFOLD`: accessible working page with title, input and add button.
- `CRUD`: add, persist, toggle completed, delete, persist deletions.
- `FILTERS`: All/Active/Completed filters and accurate empty states.

The planner creates descriptions/acceptance criteria for these milestones.
The reviewer independently tests each milestone in an actual browser.
Do not proceed to the next milestone if the current milestone fails.
