# Parity checklist — chatbot, brokerages and settings (Wave 2, agent "app", area 2)

Ports `mobile/lib/features/chatbot/{application,presentation}/**`,
`mobile/lib/features/brokerages/{application,presentation}/**` and
`mobile/lib/features/settings/{application,presentation}/**`. Tests:
`test/features/chatbot/chat_models_test.dart` (navigate parts; the model parts are in the data
layer), `test/features/settings/notification_prefs_controller_test.dart`,
`test/features/settings/notification_settings_screen_test.dart` (behaviour).

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## chatbot_controller.dart → `Features/Chatbot/Model/ChatbotModel.swift`

- [x] `ChatbotState` fields: isOpen, isFullscreen, conversations, activeConversation, busy, error, conversationListOpen, models, modelsLoaded, toolCatalog, lastModelId, bootstrapped; `messages`, `needsModel` (no conversation, or empty/nil model id), `pendingConfirmationMessage` (last pending).
- [x] Bootstrap once when signed in: refresh conversations; with no active conversation and a non-empty list, load the first; then load the tool catalog (only when empty; errors ignored).
- [x] Sign-out resets to a blank state; sign-in bootstraps fresh → native form: the model is created when the dock appears signed in and dropped when it disappears signed out (feature-local holder), so a lock teardown keeps it.
- [x] `_refreshConversations`: `GET /chatbot/conversations`; failure → `error = e.toString()`.
- [x] `_loadConversation(id)`: `GET /chatbot/conversations/:id`; sets active + `lastModelId` (when the convo's model id is non-empty); failure → error.
- [x] `open` / `close` (also leaves fullscreen) / `toggleFullscreen` / `minimise` / `clearError` / `toggleConversationList`.
- [x] `selectConversation(id)`: closes the list, loads the conversation.
- [x] `refresh()` = refresh conversations.
- [x] `startNewConversation(modelId:title:)`: clears error; `POST /chatbot/conversations` with `model_id` = given ?? lastModelId (and `title`) only when non-null; sets active + lastModelId; refreshes the list; returns it.
- [x] `setModel(id)`: no conversation → start one with that model; else `PATCH {model_id}`; sets active + lastModelId; failure → error.
- [x] `setAutoConfirmSafe(value)`: `PATCH {auto_confirm_safe_tools}`; failure → error.
- [x] `clearConversation()`: busy + error cleared; `POST …/clear`; sets active; refresh list; failure → busy false + error.
- [x] `deleteConversation()`: busy; `DELETE …/:id`; clears active; refresh list; opens the first remaining; failure → busy false + error.
- [x] `send(text)`: trimmed, empty → no-op; starts a conversation when none; optimistic user message `temp-<ms>` (status `sending`, now); busy + error cleared; `POST …/turn {content}`; replaces the optimistic message with the server's messages; busy false; then re-fetches the conversation and the list (errors ignored).
- [x] `send` failure: the optimistic message becomes `failed` (other fields kept), busy false, error set.
- [x] `confirmTool(messageId, approved)`: busy; `POST …/confirm-tool {message_id, approved}`; re-fetch the conversation; failure → error.
- [x] `loadModels()`: once (`modelsLoaded`); `GET /models`; failure → error.

## chatbot_dock.dart → `Features/Chatbot/Views/ChatbotDockView.swift`

- [x] Hidden when signed out (`ChatEntrySlot`, core).
- [x] Collapsed violet pulsing FAB (`smart_toy`, glow) bottom-right above the tab bar → native form: a floating `.glassProminent` circular button with the `smart_toy` symbol, no glow or pulse.
- [x] Expanded near-fullscreen panel → native form: a `.sheet` with `.medium` / `.large` detents and a drag indicator; minimise = dismiss; `isFullscreen` selects the large detent.
- [x] Panel tap outside fields drops the keyboard → `.scrollDismissesKeyboard(.interactively)`.
- [x] Header: title `activeConversation.title ?? 'Assistant'`, model name upper-cased when present → native form: navigation title + subtitle.
- [x] Header buttons: Settings (`settings`), Clear conversation (`delete_sweep`), Minimise (`expand_more`) → toolbar items with accessibility labels `Settings`, `Clear conversation`, `Minimise`.
- [x] Clear (header) confirm: `Clear conversation` / `This will delete all messages. This cannot be undone.` / `Clear` (danger).
- [x] Conversation bar only when > 1 conversation: `N conversations` toggles the list (`history` / `expand_less`), `New` starts one; rows `title ?? 'Untitled'`, `N msg`, active highlighted; tap selects → native form: a toolbar `Menu` (`history` glyph) titled `N conversations` with `New` and one checkmarked row per conversation.
- [x] Body: needs a model → first-run picker; no messages → empty state; else message list.
- [x] Empty state: bot tile, `How can I help?`, `Ask about your portfolio, run a backtest, link a brokerage, or just say hi. I can show charts, tables, and run actions on your behalf with your approval.`, pills `List my instances`, `Show my portfolio over the last month`, `How many backtests have I run?` (tap sends).
- [x] Message list: pending-confirmation messages render as tool-call cards; others as bubbles; `Thinking…` with three pulsing dots while busy; error banner with Retry (= clearError) after the list.
- [x] Scroll: snap to the bottom on open; follow new messages and the keyboard.
- [x] Composer: placeholder `Pick a model first…` when a model is needed, else `Ask me anything…`; disabled when a model is needed; busy disables and shows a spinner in the send button.
- [x] Navigate directives: `navigate` blocks with a non-empty route fire once per `messageId:route`, only for allowed prefixes (`/dashboard`, `/instances`, `/backtests`, `/strategies`, `/brokerages`, `/agent-runs`, `/nexus`, `/models`, `/token-usage`, `/settings`; exact or `prefix/…`; must start with `/`, not `//`) → native form: the sheet is dismissed and `AppRouter.open(path)` navigates (the brief's ruling).
- [x] In-tree confirm overlay (`ChatConfirmOverlay`) → native form: `.confirmAlert` (alert with Cancel and a role).

## chat_message.dart → `ChatMessageBubble`

- [x] User right-aligned accent bubble (18/18/18/4 corners), medium weight; assistant left, secondary surface, Markdown; tool left, monospace, `name → ` prefix, content truncated at 200 chars with `…`, 2 lines.
- [x] Bubble max width 82 % of the screen.
- [x] Tool-call chips (`build` + upper-cased name) on assistant messages with tool calls.
- [x] The bubble shows when the content is non-empty, or when there are neither visible blocks nor tool calls.
- [x] Navigate blocks hidden from the visible blocks.
- [x] Timestamp `HH:mm` under non-tool messages → see Ruling (local time).
- [x] Bubble shadow → native form: none (no coloured shadows; flat fills).

## chat_rich_block.dart

- [x] `markdown` → `MarkdownText`.
- [x] `table`: optional upper-cased title; headers `label ?? key`; cells `row[key ?? label]`; numbers with 0 decimals when integral else 2; horizontal scroll.
- [x] `chart` / `portfolio` / `price`: portfolio flat `timestamps` + `values` (line, index x); series `points[].value` (line); generic `data` `[[x, y]]` or `[y]`; `chart_type` `bar` (default) → bars else lines; series name `symbol ?? name ?? 'Series i'`; palette primary/success/info/warning/danger; `(no chart data)` when empty; 200 pt tall → native form: Swift Charts, flat colours, no gradients.
- [x] `stat`: upper-cased label, value, trend `up` (`trending_up`, green) / `down` (`trending_down`, red), optional detail.
- [x] `navigate`: `north_east` + `Navigated to ` + monospace route.
- [x] Other types: `(unsupported block: $type)` italic.

## chat_tool_call.dart → `ChatToolCallCard`

- [x] Tiers: safe → accent, `Run tool`, `play_circle`; destructive → red, `DESTRUCTIVE — confirm carefully`, `warning`; write (default) → orange, `This will change your workspace`, `play_circle`.
- [x] Header: upper-cased tier label, tool name (`tool` fallback), description.
- [x] Arguments: 2-space indented JSON, collapsible (`Arguments`), hidden when empty.
- [x] Destructive: `Type CONFIRM to proceed` + field; Approve enabled only when it reads exactly `CONFIRM`.
- [x] Actions `Decline` and `Approve & run` (check glyph, spinner when busy); both disabled while busy.

## chat_composer.dart

- [x] 1…6 lines, multiline; send trims, ignores empty/busy/disabled, clears the field; send button disabled without text.

## chat_model_picker.dart

- [x] Loads models on appear; bot tile, eyebrow `WELCOME`, `Pick the model that powers me`, `I'll use this model for every reply in this conversation. You can swap it later in settings.`
- [x] Loading `Loading models…`; empty `No models configured yet. Add one on the Models page.` (warning); else `MODEL` + selectable rows (name, `provider · model` with stray `·` trimmed, check when selected).
- [x] `Start chatting` (check glyph, busy) enabled once one is selected; confirms → `setModel(id)`.

## chat_settings_sheet.dart → `ChatSettingsSheet`

- [x] In-tree scrim + bottom panel → native form: a `.sheet` (`.medium`/`.large`, drag indicator) with an inset-grouped `Form`; title `Chat settings`, Close button.
- [x] Loads models on appear.
- [x] `MODEL`: loading / empty (as the picker) / rows; the current model is checked and inert; busy disables; tap → `setModel`.
- [x] `TOOLS`: `Auto-run safe (read-only) tools` toggle with `When on, the assistant can call read-only tools like list_instances without asking. Mutating tools always require approval.`; disabled without a conversation.
- [x] `CONVERSATION`: `New` (starts a conversation, closes), `Clear` (warning; confirm `Clear conversation` / `This deletes all messages in this conversation. This cannot be undone.` / `Clear`; closes), `Delete` (danger; confirm `Delete conversation` / `This permanently deletes this conversation. This cannot be undone.` / `Delete`; closes); Clear and Delete disabled without a conversation.
- [x] `TOOLS THE ASSISTANT CAN USE` (only with a catalog): groups `Read-only · auto-runnable` (green), `Confirm to run` (orange), `Destructive · always confirm` (red) with `(count)`, expandable to the tool names; name = `name ?? function ?? tool`, safety default `write`, nameless tools skipped → native form: `DisclosureGroup`s.

## brokerages_controller.dart / brokerages_screen.dart → `Features/Brokerages`

- [x] `BrokeragesController` (autoDispose): `GET /brokerages` on build; `refresh()` → loading then reload.
- [x] Back + refresh app-bar buttons → native form: navigation back button + a `refresh` toolbar button.
- [x] Pull to refresh.
- [x] Header: eyebrow `Brokerages`, title `Linked Accounts`, `Manage your brokerage connections.`, `Link Brokerage` (`add`).
- [x] Loading skeleton → native form: redacted placeholder cards.
- [x] Error → error banner with Retry (refresh).
- [x] Empty: `account_balance`, `No brokerages linked yet`, `Link an Alpaca account to start stock trading.`, action `Link your first brokerage`.
- [x] Card: brand logo tile, account name, badge `ALPACA · Paper|Live` (info/warning) or upper-cased type (success), status dot + `status ?? 'unknown'` (active green, expired red, else orange).
- [x] Details: `Account #: …`, `Last refreshed: …` (parsed → `fmtDateTime`, raw on failure), `Error: …` (red).
- [x] Actions: `Edit` (opens the sheet in edit mode), `Remove` (danger) → confirm `Remove "<name>"?` / `This will unlink the brokerage account. This cannot be undone.` / `Remove` → `DELETE /brokerages/:id`, then refresh.

## link_brokerage_sheet.dart → `LinkBrokerageSheet`

- [x] Modal bottom sheet → native form: `.sheet` with a `NavigationStack` + `Form`; title `Link Brokerage Account` / `Edit Brokerage Account`; Close disabled while submitting.
- [x] Tabs `Alpaca` / `Binance.US` (create mode only; edit opens the account's type) → native form: segmented `Picker`; switching clears the status message.
- [x] Edit prefill: alpaca → name, paper, feed (`iex` default); binanceus → name, paper.
- [x] Alpaca fields: `Account Name` (`e.g. My Paper Trading`), `API Key ID` (`PKXXXXXXXXXXXXXXXXXXXXXXXX`, mono), `Secret Key` (+ ` (leave blank to keep existing)` in edit; placeholder `Leave blank to keep existing` / `●●●…`, obscured, mono).
- [x] `Paper trading` toggle + `(paper-api.alpaca.markets)` / `(api.alpaca.markets)`.
- [x] `Market Data Feed`: `IEX (free — Basic Market Data)` / `SIP (paid — Algo Trader Plus)`; note `Paper accounts have free IEX. Live accounts need a subscription for IEX or SIP.`
- [x] Test: no form creds and not editing a stored account → result `{ok: false, summary 0/0/0, tests [], hints ['Fill in both API Key ID and Secret Key first.']}`; else `POST` test with `{key, secret, paper, alpaca_data_feed}` or `{brokerage_id, paper, alpaca_data_feed}`; failure → hint `Network error: $e`. Button `Test` / `Testing…` (`network_check`), disabled while submitting or testing.
- [x] Test panel: running `Running 5-endpoint probe against Alpaca…`; summary `Probe could not run — see hints below.` (total 0, warning) / `All N tests passed` / `F of N tests failed`; close; per-test rows (name mono, `HTTP status`, message); `Hints` list.
- [x] Alpaca save validation: `Account name is required`; create: `API Key ID is required`, `Secret Key is required`.
- [x] Pre-save test (form creds, not bypassed): `Validating credentials…`; ok → save; not ok → panel + `Credential test failed — review below. Tap "Save Anyway" to bypass.`; error → `Pre-save test errored ($e); tap Save again to bypass.`
- [x] Save body: edit `{account_name?, key?, secret?, paper, alpaca_data_feed}` (non-empty only) → `PUT /brokerages/:id`; create `{brokerage_type: 'alpaca', account_name, key, secret, paper, alpaca_data_feed}` → `POST /brokerages`.
- [x] Success `Account updated!` / `Account linked!` (green), refresh the list, dismiss after 1.2 s; failure → the error text.
- [x] `Save Changes` / `Link Account` (busy); `Save Anyway` (orange) when the panel shows a failed result and no test is running.
- [x] Binance.US: fee note `Spot fees: 0.00% maker / 0.02% taker — ~12× cheaper than Alpaca crypto (0.25%), which is what makes high-frequency strategies viable. Create a read+trade API key at binance.us (no withdrawal permission needed).`; fields `Account Name` (`e.g. Binance.US Paper`), `API Key` (`Binance.US API key`), `Secret Key`; `Paper trading` + `(simulated fills vs. live price)` / `(live signed orders · real money)`; live warning `⚠ Live account — instances bound here place real Binance.US MARKET orders with real funds.`
- [x] Binance.US validation `Account name is required` / `API Key is required` / `Secret Key is required`; body `{brokerage_type: 'binanceus', account_name, key, secret, paper}` or the edit subset; `Cancel` + `Save Changes` / `Link Account`.

## settings_controller.dart / settings_screen.dart → `Features/Settings`

- [x] Lock toggle through `AppLock.enable()/disable()`; busy spinner; failure → `Biometric authentication failed or unavailable.` → native form: toast.
- [x] Biometric availability: `Checking…` (spinner), `Lock the app when you leave` / `No biometrics enrolled on this device` (toggle disabled).
- [x] `Auto-lock after` / `Time before the app locks in background`, value = timeout label, only when the lock is on; sheet `Auto-lock timeout` / `Lock the app after this much time in the background.` with the `LockTimeout` options (check on current) → `setTimeout` → native form: a `Menu` with an inline picker, the sheet's line as its header.
- [x] `Require unlock on launch` / `Always prompt when the app is opened fresh`, `ON` / `OFF` badge.
- [x] `Notifications` / `Discord & iOS push per alert category` → `/settings/notifications`.
- [x] `Signed in as` + username.
- [x] `Log out` / `Sign out of your account` → confirm `Log out` / `You will be signed out of IntelliStock. Your data stays on the server.` / `Log out` (danger) → `session.clear()`.
- [x] `Re-run Onboarding` / `Reset and walk through setup again` → confirm `Re-run Onboarding` / `This will reset your onboarding state on the server. Continue?` / `Reset & Re-run` (warning) → `POST /onboarding/reset` → `setUser(user)` → onboarding; failure → `Failed to reset onboarding: $e`.
- [x] `Version` = `version+build` (`…` / `Unknown`).
- [x] `Backend` = base URL → `/connect`.
- [x] `Open-source licenses` / `Third-party package licenses` → Flutter's license page → native form: see Ruling.
- [x] Section labels `SECURITY`, `PREFERENCES`, `ACCOUNT`, `ABOUT`; GlassCard sections → native form: inset-grouped `List` sections with tinted icon tiles; no appearance setting (HIG).

## notification_prefs_controller.dart / notification_settings_screen.dart

- [x] `NotificationPrefsController` (autoDispose): load; `toggle(category, channel, value)` → `Preferences not loaded yet` when unloaded; optimistic update, `PUT` the full matrix, adopt the saved copy; revert + error string on failure.
- [x] `sendTest(channel)` delegates.
- [x] Loading spinner; error `Failed to load preferences\n$e` (red).
- [x] `TEST DELIVERY`: `Send a sample notification to confirm a channel works.`; `Test Discord` (`discord`), `Test iOS push` (`notifications`).
- [x] Test outcome: push not ok with 0 devices → `No iOS device registered yet — tap "Enable push on this device".`; push not ok with devices → `Push failed: <first error reason>` or `Push not delivered — check APNs setup.`; else `<Discord|iOS push> test sent ✓` / `… test could not be sent`; exception → `<label> test failed: $e`; push always refreshes the device list afterwards.
- [x] `REGISTERED DEVICES`: `Checking registered devices…`; `Could not load devices: $e`; empty `No devices registered yet.` + `Tap "Enable push on this device" and allow notifications. Requires a physical device with the app installed (push doesn't work in the simulator).`; rows `…<suffix>`, `IOS · env · seen <date>`, Remove (`delete`).
- [x] Remove device → `unregister` → refresh → `Device removed` / `Could not remove: $e`.
- [x] `Enable push on this device` → `Requesting push permission…`, `PushService.enable()`, wait 2 s, refresh devices.
- [x] Grouped routing: API `types` in first-appearance group order, else the 19 built-in categories under `Notifications`; per row label, description, `Discord` and `iOS push` switches; toggle failure → `Could not save: $err`.
- [x] SnackBars → native form: toasts (success / error style).

## Copy changes (title-style capitalisation)

- `Approve & run` → `Approve & Run`; `Start chatting` → `Start Chatting`; `Chat settings` → `Chat Settings`;
  `Link your first brokerage` → `Link Your First Brokerage`; `Test iOS push` → `Test iOS Push`;
  `Enable push on this device` → `Enable Push on This Device`; `Log out` (button) → `Log Out`;
  `Save Anyway`, `Save Changes`, `Link Account`, `Link Brokerage` unchanged.
  Messages that quote a button keep the Dart wording.

## Rulings

- Ruling: the chatbot model is held in a feature-local `ChatbotSession` keyed to the services graph (created when the dock appears signed in, dropped when it disappears signed out) instead of `AppServices` — the dock's view is torn down by the lock, and the Dart provider was keepAlive — cost if wrong: none; it can move into `AppServices` at the merge (requested in the report).
- Ruling: navigate directives fire only for messages that arrive in a live turn (send / confirm-tool); history loaded at bootstrap, on select or after a delete is marked handled — Dart's dock scanned every loaded message, so opening the app replayed an old conversation's navigation — cost if wrong: an old directive no longer re-navigates on launch.
- Ruling: a navigate directive dismisses the chat sheet and calls `AppRouter.open(path)` for the last new route (Dart's `go` per route ended on the last) — the brief's ruling — cost if wrong: none.
- Ruling: message timestamps show local `HH:mm` — Dart parsed the backend's `…Z` strings to UTC `DateTime`s and printed their UTC hour, so server messages showed UTC while the optimistic one showed local time — cost if wrong: the clock differs from Flutter by the UTC offset.
- Ruling: the panel opens at the large detent (the Dart panel covered the screen); `isFullscreen` stays as state only, as in Dart, where the mobile dock never read it — cost if wrong: none.
- Ruling: `startNewConversation` failures from the conversation menu or the settings sheet land in `error` (Dart left the future's error unhandled) — cost if wrong: an extra banner on a failed create.
- Ruling: Re-run Onboarding relies on the gate (the reset user has `has_completed_onboarding: false`) instead of also calling `go('/onboarding')`, which would have left `.onboarding` on the More stack after finishing — cost if wrong: none.
- Ruling: `Open-source licenses` opens a sheet with the app name, version and a note that the native app bundles no third-party packages — Flutter's license page listed Flutter packages the native app no longer ships — cost if wrong: the copy on that sheet is new.
- Ruling: the auto-lock timeout sheet is a `Menu` with an inline picker whose section header carries the sheet's line — native form — cost if wrong: the sheet title `Auto-lock timeout` is only the picker's accessibility label.
- Ruling: confirmed actions (clear / delete conversation, remove brokerage, log out, re-run onboarding) pass `onError` and disable their trigger while running (orchestrator heads-up); a `CancellationError` is never shown as an error — cost if wrong: none.
- Ruling: the message bubble, composer and cards use flat fills; the composer is a Liquid Glass capsule (a floating control over the scrolling messages), content stays solid — cost if wrong: none.
