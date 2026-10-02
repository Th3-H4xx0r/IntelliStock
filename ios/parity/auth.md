# Parity checklist — auth, onboarding and the login coin (Wave 2, agent "app", area 1)

Ports `mobile/lib/features/auth/{application,presentation}/**`,
`mobile/lib/features/onboarding/{application,presentation}/**` and the coin asset
`mobile/assets/models/coin.glb`. Tests: `test/features/auth/{login_state,login_entrance,login_coin}_test.dart`.

Legend: `[x]` ported as-is · `[x] → native form: …` deliberately changed in form · `[ ]` open.

## auth_controller.dart → `Features/Auth/Model/LoginModel.swift`

- [x] `LoginState(isLoading=false, errorMessage=nil, succeeded=false)`, `hasError`, `copyWith` (omitted `errorMessage` clears it), `toString` `LoginState(isLoading: …, errorMessage: …, succeeded: …)` → `LoginState` + `description`.
- [x] `login(username, password, holdBeforeCommit:)`: state → loading + error cleared; `POST /auth/login` body `{username, password}` (data layer, `AuthRepository.login`).
- [x] Token = `access_token as String? ?? ''`; user = `user as Map?` (non-map → nil).
- [x] On accept: state = `LoginState(succeeded: true)` BEFORE the hold, then waits `holdBeforeCommit`, then `session.setSession(token, user)`; returns true.
- [x] `ApiError` → `isLoading=false`, `errorMessage = e.message`, returns false.
- [x] Any other error → `errorMessage = 'Something went wrong. Please try again.'`, returns false.
- [x] `clearError()` only when `hasError`.
- [x] AutoDispose (fresh per screen) → the view owns the model in `@State`.

## login_screen.dart → `Features/Auth/Views/LoginView.swift`

- [x] `LoginEntranceController`: `revealDelay` 450 ms after `onCoinEntranceStarted`; idempotent (ignored when shown or pending); `dispose` cancels → `LoginEntranceModel` (task-based timer, injectable sleep).
- [x] `_successHold` 1850 ms passed as `holdBeforeCommit`.
- [x] Local validation: trimmed username or password empty → `Please enter your username and password.`; otherwise clears the local error and logs in with the TRIMMED username and raw password.
- [x] Typing in either field clears the local error and the controller error.
- [x] Shown error = `loginState.errorMessage ?? _localError`.
- [x] `busy = isLoading && !succeeded`; coin phase `success` when succeeded, `working` when busy, else `idle`.
- [x] Tap on the background drops the keyboard.
- [x] Layout: coin above the form, column max width 340, sits above centre, scrolls when the keyboard is up.
- [x] Form hidden (opacity 0, slight downward offset, non-interactive) until the entrance reveals it; 420 ms ease-out-cubic fade + slide in.
- [x] On success everything below the coin collapses (620 ms) and the title and card fade out, lifting the coin.
- [x] Title `Welcome back`.
- [x] Error banner (animated in/out) with the `error` glyph above the fields.
- [x] Username field: placeholder `Username`, `person` glyph, next action, username autofill, disabled while busy.
- [x] Password field: placeholder `Password`, `lock` glyph, obscured, done action submits, password autofill, disabled while busy; eye toggle (`visibility` / `visibility_off`) disabled while busy.
- [x] Sign-in pill: `Sign In`, busy label `Signing in…`.
- [x] Biometric row only when `canCheck()` is true; hidden on error. Method: `face` → `Face ID` (Face ID glyph), else `fingerprint` → `Touch ID` (fingerprint glyph), else `biometrics`.
- [x] Row text `Unlock with $method`; switch bound to `appLock.enabled`; disabled (50 % opacity) while toggling or busy.
- [x] Toggle on → `lock.enable()`; failure → local error `Could not turn on $method.`. Toggle off → `lock.disable()`.
- [x] `redirectPath` unused by the screen (router handles it) → `AppServices.loginRedirect` / `didSignIn` (core).
- [x] AppBackground, violet `_CardBloom`, frosted `GlassCard` → native form: plain system grouped background, inset-grouped rows for the fields; no bloom, no glass on content (operator: no gradients).
- [x] `_AppTextField` wells → native form: `TextField`/`SecureField` rows with leading SF Symbols, `textContentType` `.username`/`.password`.
- [x] `_BiometricToggle` → native form: a `Toggle` row with `faceid`/`touchid` symbol.
- [x] `AuthPillButton` → shared `AuthPillButton` (`.glassProminent`).

## login_coin.dart → `Features/Auth/Model/LoginCoinModel.swift` + `Views/LoginCoinView.swift`

- [x] `CoinPhase` idle / working / success.
- [x] Viewer hidden until a clip is told to play; 180 ms ease-out fade-in.
- [x] 1100 ms kick: if neither loaded, failed nor started, start the clip anyway (marks started + entrance).
- [x] `onEntranceStarted` fires once — on the first play, or on failure.
- [x] idle (first time) → `Intro` once, then `Idle` (looping) after 2400 ms if still idle; idle again (after a rejected sign-in) → straight to `Idle` without replaying the entrance.
- [x] working → `Spin` looping; success → `Success` once.
- [x] A phase change replays (not gated on load).
- [x] Background → pause the clip; resume → replay the current phase.
- [x] Load success → play; load failure → fallback + entrance notified. Nothing blocks sign-in.
- [x] Rendered at 470 pt, scaled to `size/470` at rest (322 default), grows to 1.0 on success over 900 ms ease-out-cubic, overflows its slot; never intercepts touches.
- [x] `coin.glb` (four clips) → native form: four USDZs (`Resources/Coin/coin_{intro,idle,spin,success}.usdz`) built by `ios/tools/coin/build_coin.sh`, because the system `usdcat` keeps only a file's first clip; the Intro file's entities play every clip, each taken from its own file's `availableAnimations`, in a RealityKit `RealityView` (virtual camera).
- [x] Violet radial light under the coin → native form: removed (operator: no gradients or glows).
- [x] `_Fallback` gradient disc with `show_chart` → native form: flat accent-tinted disc with a hairline accent ring and the `show_chart` glyph, same 62 % silhouette.

## onboarding_controller.dart → `Features/Onboarding/Model/OnboardingModel.swift`

- [x] `OnboardingStep` welcome, about, addModel, linkBrokerage, createInstance, connect, complete.
- [x] `OnboardingState`: stepIndex 0, direction forward, busy false, error nil, counts 0; `currentStep`, `isFirstStep`, `isLastStep`; `copyWith` with nullable-error sentinel.
- [x] `next()` no-op on last; `back()` no-op on first; both set direction and clear the error; `skip()` = `next()`.
- [x] `updateCounts(models:brokerages:instances:)`.
- [x] `loadState()`: `GET /onboarding/state`; counts from `counts.{models,brokerages,instances}` (`num?.toInt() ?? 0`); errors swallowed.
- [x] `finish()`: busy + error cleared; `POST /onboarding/complete`; `user` map → `session.setUser`; busy false; true. Error → busy false, `error = e.toString()`, false.
- [x] keepAlive `NotifierProvider` → native form: the screen owns the model in `@State` (fresh on each visit). Ruling below.

## onboarding_screen.dart → `Features/Onboarding/Views/OnboardingView.swift`

- [x] Loads counts on mount.
- [x] Step labels `Welcome`, `About`, `Model`, `Brokerage`, `Instance`, `Connect`, `Done`; skippable = index 2…5.
- [x] Top bar: `auto_awesome` tile, eyebrow `INTELLISTOCK`, `Welcome flow`, `Exit` + `close`.
- [x] Exit → confirm `Exit onboarding?` / `Your saved models, brokerages, and instances stay configured. You can re-run this flow later from the Settings screen.` / `Exit` (warning, not destructive) → go `/dashboard`.
- [x] Progress header: numbered circles (done → check, current → 2 pt ring, upcoming → faint), connectors filled before the current step, 300 ms; scrolls horizontally.
- [x] Pages switch programmatically only (no swipe), 350 ms slide in the travel direction.
- [x] Footer: `Back` (+ `arrow_back`) when not first and not last; `Skip for now` when skippable; primary `Next` / `Open dashboard` (+ `arrow_forward` unless busy, spinner when busy); everything disabled while busy.
- [x] Next on the last step → `finish()`; success → go `/dashboard`.
- [x] `finish()` failure → `error` is set but never shown (Dart screen never read `state.error`) → kept: not shown.
- [x] AppBackground → native form: plain grouped background; when pushed inside a tab stack the navigation and tab bars are hidden so it covers the shell as the Dart route did.

## steps

- [x] `onboarding_form_widgets.dart` `OnboardingField` (label, hint, obscure, enabled, error text) → native form: `TextField`/`SecureField` rows in an inset-grouped `Form` section, error text as a red footnote.
- [x] `OnboardingMessageBanner` (success/danger text on a tint) → native form: a tinted inline row (green / red).
- [x] Welcome: pulsing ring (2 s, reverse) around the 72 pt app logo; title `Welcome to IntelliStock` with a per-word stagger (400 ms each, 80 + 60·i ms delay, slide-up fade); greeting `Hey $username — let's get your autonomous trading workspace dialled in. We'll set up an LLM model, link a brokerage, and spin up your first instance.`; tiles `LLM Models` / `OpenAI · Gemini · Azure · NVIDIA` (memory), `Brokerages` / `Alpaca` (account_balance), `Instances` / `Live or paper, fully autonomous` (rocket_launch).
- [x] About: eyebrow `WHAT IS INTELLISTOCK`, `AI-powered autonomous trading.`, body; 2×2 cards (`LLM Models`, `Strategies`, `Instances`, `Brokerages` with their copy and colours primary/info/success/warning); flow row Model → Strategy → Instance → Brokerage.
- [x] Add model: eyebrow `STEP 1 · ADD A MODEL`, `Pick the brain that powers your trades.`, body; `SAVED THIS SESSION` tiles (`name`, `provider · model`); fields `Model Name *` (`e.g. gemini-flash-prod`), `Provider` (gemini, openai, azure, nvidia, ollama, bedrock, claude-cli, codex-cli; default gemini), `Model ID *` (`e.g. gemini-2.5-flash`), `API Key` (`sk-…`, obscured); button `Test & save` / `Saving…` (`bolt`), enabled only with name + model.
- [x] Add model submit: `Name and model are required.` guard; `Saving model…`; `POST /models` `{name, provider, model}` + `api_key` only when non-empty; success `Model "$name" saved.` (name = response `name` ?? body name), clears the form, provider back to gemini, models count + 1; `ApiError.message` / `e.toString()` on failure.
- [x] Link brokerage: eyebrow `STEP 2 · LINK A BROKERAGE`, `Connect your trading account.`, `Connect Alpaca in paper mode first to test without real money.`; saved tiles (`account_name`, `brokerage_type`); fields `Account Name *` (`e.g. alpaca-paper`), `API Key *` (`PK…`), `API Secret *` (`Secret…`, obscured), `Paper mode` toggle (default on); button `Link brokerage` / `Saving…` (`link`), always enabled.
- [x] Link brokerage submit: `Name, API key, and secret are required.` guard (trimmed); `Saving brokerage…`; `POST /brokerages` `{account_name, brokerage_type: 'alpaca', api_key, api_secret, paper}` (trimmed); success `Brokerage "$name" linked.`, clears + paper back on, brokerages count + 1; error message on failure.
- [x] Create instance: eyebrow `STEP 3 · CREATE AN INSTANCE`, `Spin up your first instance.`, body; saved tiles (`name ?? instance_id`, `instance_id`); `Instance ID *` (`e.g. my-bot`) validated live against `^[a-z0-9_-]+$` → `Only lowercase letters, digits, - and _ allowed.` (empty → no error); hint `Lowercase letters, digits, hyphens and underscores only.`; `Display Name *` (`e.g. My First Bot`); cadence chips `1min` `5min` `15min` `1hr` (default 5min) → `1m` `5m` `15m` `1h`; button `Create instance` / `Creating…` (`rocket_launch`), enabled with id + name + no id error.
- [x] Create instance submit: `Creating instance…`; `POST /instances` `{instance_id, name, cadence}`; success `Instance "$name" created.`, clears, cadence 5min, instances count + 1; error message on failure.
- [x] Connect: eyebrow `STEP 4 · CONNECT THE PIECES`, `How a trade actually flows.`, body; flow nodes Model/`Reasons over signals`, Strategy/`Picks tickers + capital`, Instance/`Runs on a cadence`, Brokerage/`Executes orders`; card `LINK AN INSTANCE TO A BROKERAGE`.
- [x] Connect load: `GET /instances` + `GET /brokerages` in parallel; instances = `instances ?? items ?? []` maps; brokerages = `accounts ?? items ?? []` maps; a single option is pre-selected (`instance_id ?? id`, brokerage `id`); loading `Loading resources…`; failure `Could not load: $error` + `Retry`; empty `You'll need at least one instance and one brokerage to link them here. Skip for now and do it later from the Instances page.`
- [x] Connect pickers: `Instance` (label `name ?? instance_id ?? id`), `Brokerage` (`account_name (brokerage_type)`), placeholder `Pick one…`, disabled while busy → native form: `Picker` rows (menu style).
- [x] Connect submit: `Select both an instance and a brokerage.` guard; `Linking…`; `POST /instances/{encoded id}/link-brokerage` `{brokerage_id}`; success `Linked! Your instance can now place orders through this brokerage.`; error message on failure; button `Link brokerage to instance` / `Linking…` (`link`), enabled only with both selected.
- [x] Complete: ray burst (14 rays, 1200 ms) + elastic check (600 ms) → native form: SF Symbol `checkmark.circle` with a bounce symbol effect on a flat tinted disc (no glow shadow; the rays are dropped as decoration); `You're all set.`; body; count tiles `MODELS` / `BROKERAGES` / `INSTANCES` with live counts.

## Copy changes (title-style capitalisation)

- `Test & save` → `Test & Save`; `Link brokerage` → `Link Brokerage`; `Create instance` → `Create Instance`;
  `Link brokerage to instance` → `Link Brokerage to Instance`; `Open dashboard` → `Open Dashboard`;
  `Skip for now` → `Skip for Now`.

## Rulings

- Ruling: four USDZ files, one per clip, built from the GLB by `ios/tools/coin/` — the system `usdcat` drops every clip but the first — cost if wrong: four ~310 KB files instead of one.
- Ruling: every per-clip GLB is named `coin.glb` before conversion so each USD layer's root prim is `/coin` and every clip binds to the same entity paths — cost if wrong: none.
- Ruling: the coin's violet light pool and the fallback's gradient are dropped/flattened — operator: no gradients or glows — cost if wrong: the coin reads slightly flatter on the plain background.
- Ruling: `OnboardingModel` lives in the screen's `@State`, not app-wide — Dart's PageController always restarted at page 0 while its keepAlive state kept the old step index, so the header and the page disagreed on re-entry; a fresh model keeps them in step — cost if wrong: a re-entered flow starts at Welcome (which is what the Dart pages showed anyway).
- Ruling: the onboarding step forms are `Form` sections; the presentational steps (welcome, about, complete) are scrolling cards — Settings-style rows where the content is rows — cost if wrong: none.
- Ruling: a keychain failure while committing an accepted sign-in brings the form back with `Something went wrong. Please try again.` — Dart's copyWith kept `succeeded`, leaving the form collapsed and the error invisible; the orchestrator asked for the error to show — cost if wrong: none (the gate never moved).
- Ruling: a cancelled login/onboarding request (`CancellationError`) is not reported as an error; spinners stop and the state is otherwise unchanged (orchestrator, Wave 1 review) — cost if wrong: none.
- Ruling: `access_token` that is not a string is read with `toString()` and a non-map `user` reads as nil, where Dart threw a TypeError into the generic message — the data layer's lenient-cast ruling — cost if wrong: a malformed reply signs in with a junk token and is then bounced by the first 401.
- Ruling: the coin USDZs are re-timed to 60 whole time codes per second (`retime_usda.py`) — RealityKit samples whole codes and the converter writes 1 code/s, so the Success turn stopped at ~103° until re-timed; verified on the simulator that all four clips play (entrance from a dot, idle bob, spin wobble, the turn-over to the IntelliStock mark) — cost if wrong: none.
- Ruling: the Complete step's ray burst and glow are replaced by a symbol bounce on a flat disc — no glows — cost if wrong: a quieter celebration.
