# Redesign parity — Wave R2, agent "app"

Screens: Brokerages (and the link sheet), LLM Models (list, editor, CLI panels), Token Usage,
Agent Runs, Settings, Notifications, More, Connect, Login, Onboarding, and the chat sheet.

The rule is the spec's (`docs/superpowers/specs/2026-10-02-ios-ui-redesign.md`): behaviour is
unchanged. Every endpoint, body, guard, confirmation, poll and string stays. Only placement, style,
title-case capitalisation and repeated titles or eyebrow blocks change. Every old button is listed
below with where it went. Nothing became unreachable.

## Moved buttons

| Screen | Old button | New location |
|---|---|---|
| More | Nine destination rows | Same rows, now with Settings-style icon tiles |
| More | Sign Out | Same row, now with a red tile |
| Brokerages | Refresh (toolbar ↻) | Pull to refresh |
| Brokerages | Link Brokerage (header pill) | `+` toolbar item, "Link Brokerage" |
| Brokerages | Edit (card) | Row tap; swipe (trailing) Edit; context menu Edit |
| Brokerages | Remove (card) | Swipe (trailing) Remove; context menu Remove. The confirmation is kept |
| Brokerages | Link Your First Brokerage (empty state), Retry (error) | Unchanged |
| Link sheet | Close X (trailing) | Leading toolbar item, with the same disabled-while-saving guard |
| Link sheet | Cancel (Binance form, bottom) | Folded into the leading Close: the same action (dismiss) and the same guard |
| Link sheet | Link Account / Save Changes (bottom, prominent) | Trailing toolbar checkmark (`role: .confirm`). VoiceOver reads "Link Account" or "Save Changes". The double-submit lock is kept |
| Link sheet | Test (bottom, bordered) | "Test" button row (spinner and "Testing…" while it runs) |
| Link sheet | Save Anyway (bottom) | "Save Anyway" button row under Test |
| Link sheet | Hide test results (X in the panel) | "Hide" in the results section's header |
| Link sheet | Brokerage segmented picker, Market Data Feed picker, Paper toggles | Unchanged |
| Models | Refresh (toolbar ↻) | Pull to refresh |
| Models | Add Model (header pill) | `+` toolbar item, "Add Model" |
| Models | Edit (pencil) | Row tap; swipe (trailing) Edit; context menu Edit |
| Models | Delete (trash) | Swipe (trailing) Delete; context menu Delete. The confirmation is kept |
| Models | Test CLI connection (cable, Claude Code CLI rows only) | Swipe (leading) "Test CLI Connection"; context menu. The spinner and result line still show on the row |
| Models | Add Model (empty state), Retry (error) | Unchanged |
| Model editor | Close X (trailing) | Leading toolbar: "Cancel" before a save, "Close" after one (the X and Cancel both dismissed) |
| Model editor | Cancel (bottom bar) | Leading toolbar "Cancel", disabled while saving |
| Model editor | Close (bottom bar, after a save) | Leading toolbar "Close" |
| Model editor | Test Only (bottom bar, not for Claude Code CLI) | "Test Only" button row ("Testing…" while it runs) |
| Model editor | Test & Save / Test & Update (bottom bar, prominent) | Trailing toolbar prominent button, hidden after the save as before |
| Model editor | Bedrock region chips (nine capsules) | "Common regions" menu under the AWS Region field. Picking a region fills the field, as a chip did |
| Model editor | Picker Refresh ("Refresh" / "Loading..."), Pricing disclosure, Keep Alive disclosure | Unchanged |
| CLI panels | Refresh status (glyph) | Unchanged: the panel's first line |
| CLI panels | Re-authenticate Claude, Cancel, Submit Code, Sign Out of Claude, Copy, the URL link | Unchanged, inside the panel row (the tinted box is gone) |
| CLI panels | Install Codex CLI, Sign In with OpenAI, Cancel, Sign Out of OpenAI, Copy | Unchanged, inside the panel row |
| Token Usage | Refresh (toolbar ↻ with spinner) | Pull to refresh. The 10 s poll is unchanged |
| Token Usage | Range (24h / 7d / 30d) | Unchanged, at the top of the list |
| Token Usage | Run row (backtest) | `NavigationLink` row to the same route |
| Token Usage | Recent call row | Row tap opens the same raw-JSON sheet |
| Token Usage | Retry (error), Close (call sheet) | Unchanged |
| Agent Runs | Start / Pause / Unpause / Stop (tinted bordered buttons) | Button rows in the controls section. Stop is red. They are disabled with a spinner while busy |
| Agent Runs | Countdown ring's cancel square | Unchanged, in its own row |
| Agent Runs | Per page ("20/page" capsule menu) | Toolbar menu, "Per Page" |
| Agent Runs | Refresh (glyph) | Pull to refresh |
| Agent Runs | Mark Stopped (small bordered button on a stale run) | "Mark Stopped" red button row under that run |
| Agent Runs | Pager ‹ 1 … › (mini bordered buttons) | The same buttons, as plain text in a pager section |
| Agent Runs | Start sheet: Cancel, Start Agent | Unchanged |
| Agent Runs | Resume sheet: preset capsules (Now, 5 min, 15 min, 30 min, 1 hr) | One button row per preset |
| Agent Runs | Resume sheet: Schedule (bordered), Cancel | Schedule is a borderless button in the same row; Cancel is unchanged |
| Settings | Biometric Lock switch, Auto-lock menu, Notifications, Backend, Log out, Re-run Onboarding, Open-source licenses | Unchanged rows. Log out and Re-run Onboarding drop the chevron (they act, they don't navigate) |
| Notifications | Test Discord / Test iOS Push (bordered pair) | Two button rows, each with its own spinner |
| Notifications | Remove device (trash glyph) | Swipe (trailing) Remove; context menu Remove. There was no confirmation before and there is none now; full swipe is off |
| Notifications | Enable Push on This Device | Unchanged button row |
| Notifications | Discord / iOS push switches | Unchanged, now two rows in each alert type's section |
| Connect | Test & Connect, URL field | Unchanged |
| Login | Sign In, Show/Hide password | Unchanged |
| Login | Unlock with biometrics (switch on a card) | A plain Settings-style row under the form, with no card |
| Onboarding | Exit (bordered capsule) | Same place, system glass button. The confirmation is kept |
| Onboarding | Back, Skip for Now, Next, Open Dashboard, step submit buttons, Retry | Unchanged |
| Chat sheet | Conversations (history menu) | Unchanged |
| Chat sheet | Settings (gear) | Toolbar menu "Chat Options", then Settings |
| Chat sheet | Clear conversation (glyph) | Toolbar menu "Chat Options", then Clear conversation (red, last). The confirmation is kept |
| Chat sheet | Minimise | Unchanged |
| Chat sheet | Suggestions, Send, Approve & Run, Decline, Start Chatting | Unchanged |
| Chat settings | Model rows, auto-run switch, New, Clear, Delete, Close | Unchanged |

## Rulings

1. **Link sheet confirm** — the trailing item is the iOS 26 checkmark, labelled "Link Account" or
   "Save Changes" for VoiceOver — as text it truncated the title to "Link Brokerage Ac…" — cost if
   wrong: one `Label` back to `Text`.
2. **Binance Cancel** — folded into the sheet's leading Close (both dismissed and both were held
   while a save ran) — cost if wrong: one button row.
3. **Link sheet status line** — the save's result shows at the top of the form, where the toolbar
   submit can be seen to answer — cost if wrong: move one section.
4. **Edit-mode account details** — the link sheet's edit mode shows Account #, Last refreshed and
   any error in an "Account" section, because the list row now carries only the number — cost if
   wrong: one section.
5. **Brokerage row text** — brand names in their own casing ("Alpaca", "Binance.US", "Kalshi")
   replace the upper-cased badge; Live/Paper and the number join the subtitle; the status word is
   capitalised ("Active", "Unknown") — capitalisation only — cost if wrong: one helper.
6. **Swipe Remove/Delete** — red by `.tint`, not `role: .destructive`, because a destructive-role
   swipe button animates the row away before the confirmation answers; full swipe is off — cost if
   wrong: the row flickers.
7. **Sheet-opening rows** — no chevron (HIG: the disclosure indicator means a push) — cost if
   wrong: add `chevron.right`.
8. **Model rows** — a private row in the `EntityRow` style that lets the title take two lines,
   since names like "AWS Bedrock / GPT OSS 120B Medium" carry the effort at the end. The old Model,
   Effort and masked Key/CLI chips moved to the editor's "Saved model" section (the spec: "the
   masked key is shown only in the editor"); the subtitle is "Azure OpenAI · Created Apr 12, 2026"
   — cost if wrong: swap to `EntityRow`.
9. **Provider glyphs** — one accent tint for all, distinct glyphs (colour has one meaning) — cost
   if wrong: a colour map.
10. **Bedrock regions** — the chips became a "Common regions" menu; that label is new — cost if
    wrong: one string.
11. **CLI panels** — each stays ONE form row. Split into rows, its `.task`, `.onAppear`, alert and
    toast modifiers would repeat per row (the comment in `LlmConfigFormSections` records the same
    trap). The tinted box is gone; the status chips are label/value lines with capitalised labels
    ("Installed", "Version", "Authenticated", "Account"); body text moved from `caption2` to
    `footnote`; the Claude CLI note is the CLI section's footer — cost if wrong: style only.
12. **Notes** — `ModelInfoBox` and the brokerage and onboarding status lines are plain rows: a
    coloured glyph and the text, no tinted box. The OpenRouter and Azure notes are section footers
    — cost if wrong: style only.
13. **Token Usage** — the in-content title and description card are gone (the nav bar has the
    title); the telemetry badge sits in the "Telemetry health" header; the KPI cards are one
    `StatGrid` (labels "Period cost", "Period calls", "Avg cost", "Recent rows"); the top-provider
    chips are `LabeledContent` rows; "Spend trend" (eyebrow) is dropped, "Cost over time" is the
    header and "Stacked by provider" the footer; the x axis shows at most four labels — cost if
    wrong: layout only.
14. **Agent Runs** — "AI Agent Runs" (the in-content title, repeating the nav title) is dropped;
    the status row reads "AI Backtest Agent" with a status dot; the description is the controls'
    footer; each cycle is a section headed by its start time (the divider is gone); the Start
    sheet's header block became its nav subtitle and footer; the Resume sheet's header block
    repeated its nav title and is dropped, its line is the footer — cost if wrong: layout only.
15. **Settings and More tiles** — one `SettingsIconTile` (white filled glyph on a solid square)
    in both lists, so they match; circled symbols lose the circle (`bitcoinsign`). "ON/OFF" is
    plain "On/Off"; Log out's title is red — cost if wrong: one component.
16. **Notifications** — each alert type is a section with two switch rows; the group name heads
    the first type of each group in `title3` bold. Pull to refresh is new (the spec: every list
    refreshes by pulling). The device subtitle reads "iOS", and any other platform is capitalised
   ("Android") instead of upper-cased — cost if wrong: small.
17. **Login** — the biometric switch is a plain row with the Settings tile and no card — cost if
    wrong: style only.
18. **Onboarding** — the tracked eyebrows are a sentence-case step line ("Step 1 · Add a model",
    "What is IntelliStock"); "INTELLISTOCK" reads "IntelliStock"; the feature cards (Welcome and
    About) are "What's New" rows; the count tiles share one card with sentence-case labels; the
    section headers are title case; Exit is a glass button — cost if wrong: style only.
19. **Chat** — the subtitle shows the model name as spelled; Settings and Clear share a "Chat
    Options" menu; the tool chips, block titles, stat labels and the tool-card tier stop
    upper-casing ("Destructive — confirm carefully"); "WELCOME" and "MODEL" read "Welcome" and
    "Model"; the settings headers are title case — cost if wrong: capitalisation only.
20. **Connect** — unchanged: it had no uppercase, tracking or card wrappers.
21. **Chat suggestions** — the three empty-state prompts stay capsule buttons: they are prompt
    chips under a single empty state (Messages-style suggestions), not a row of actions on a
    card — cost if wrong: three rows.
22. **CLI status refresh** — the panels keep their small refresh glyph. It re-probes one CLI
    inside a sheet's form, which pull to refresh cannot target; the nav-bar refresh buttons the
    spec bans are all gone — cost if wrong: move it to a menu.
23. **Destructive rows** — `InlineActionRow` draws a destructive row's title red but its glyph in
    the accent; Stop and Mark Stopped add `.tint(.red)` so both are red. The component is R1's
    and left alone — cost if wrong: none.
24. **Onboarding fixes found in review** — Next wrapped to two lines beside Back and Skip for
    Now, and the step headings clipped their first glyph at a 0 pt inset; both fixed.
25. **Header colour** — inside a section header or a list `Button`, the hierarchical `.primary`
    resolves to the header grey or the tint. The Notifications group names and the chat model
    rows use `Color.primary` — cost if wrong: none.

## Not fixed here (outside this agent's files)

- **The chat accessory over pushed Onboarding.** `OnboardingView` hides the tab bar, but the
  tab-bar bottom accessory (R1, `MainTabView`) stays visible over the welcome flow.
- **Agent Runs on the live server.** `/agent/runs` answers 500, so the live screen shows only the
  error row. The layout was checked over fixture data instead (see the report).
