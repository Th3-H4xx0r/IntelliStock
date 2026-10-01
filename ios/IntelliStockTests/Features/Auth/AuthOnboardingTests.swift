import Foundation
import Testing
@testable import IntelliStock

/// `OnboardingController` and the step forms' requests.
@MainActor
@Suite struct AuthOnboardingTests {
    private func makeSession() -> SessionStore {
        SessionStore(storage: InMemorySecureStorage(), widgetSync: WidgetSync(defaults: nil))
    }

    private func body(_ request: URLRequest?) throws -> JSON {
        try JSON(data: request?.httpBody ?? Data())
    }

    @Test func stepsAndLabels() {
        #expect(OnboardingState.steps == [.welcome, .about, .addModel, .linkBrokerage, .createInstance, .connect, .complete])
        #expect(OnboardingState.labels == ["Welcome", "About", "Model", "Brokerage", "Instance", "Connect", "Done"])
        #expect((0..<7).map(OnboardingState.isSkippable) == [false, false, true, true, true, true, false])
    }

    @Test func nextBackAndSkipStayInBounds() {
        let model = OnboardingModel(repository: { OnboardingRepository(client: DataStub().client) }, session: makeSession())
        #expect(model.state.isFirstStep)
        model.back()
        #expect(model.state.stepIndex == 0)

        model.next()
        #expect(model.state.stepIndex == 1)
        #expect(model.state.direction == .forward)
        model.back()
        #expect(model.state.stepIndex == 0)
        #expect(model.state.direction == .back)

        for _ in 0..<10 { model.skip() }
        #expect(model.state.stepIndex == 6)
        #expect(model.state.isLastStep)
        #expect(model.state.currentStep == .complete)
    }

    @Test func loadStateReadsTheCounts() async throws {
        let stub = DataStub(json: #"{"has_completed_onboarding":false,"counts":{"models":2,"brokerages":1.0,"instances":"3"}}"#)
        let client = stub.client
        let model = OnboardingModel(repository: { OnboardingRepository(client: client) }, session: makeSession())
        await model.loadState()
        #expect(model.state.modelCount == 2)
        #expect(model.state.brokerageCount == 1)
        #expect(model.state.instanceCount == 0)   // a string is not a num
        #expect(stub.last?.method == "GET")
        #expect(stub.last?.path == "/onboarding/state")

        model.updateCounts(instances: 4)
        #expect(model.state.instanceCount == 4)
        #expect(model.state.modelCount == 2)
    }

    @Test func loadStateFailureLeavesTheCounts() async {
        let stub = DataStub(status: 500, json: #"{"detail":"boom"}"#)
        let client = stub.client
        let model = OnboardingModel(repository: { OnboardingRepository(client: client) }, session: makeSession())
        await model.loadState()
        #expect(model.state.modelCount == 0)
        #expect(model.state.error == nil)
    }

    @Test func finishUpdatesTheSessionUser() async {
        let stub = DataStub(json: #"{"user":{"username":"pk","has_completed_onboarding":true}}"#)
        let client = stub.client
        let session = makeSession()
        let model = OnboardingModel(repository: { OnboardingRepository(client: client) }, session: session)
        #expect(await model.finish())
        #expect(session.hasCompletedOnboarding)
        #expect(!model.state.busy)
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.path == "/onboarding/complete")
    }

    @Test func finishFailureSetsTheError() async {
        let stub = DataStub(status: 400, json: #"{"detail":"nope"}"#)
        let client = stub.client
        let model = OnboardingModel(repository: { OnboardingRepository(client: client) }, session: makeSession())
        #expect(await model.finish() == false)
        #expect(model.state.error == "nope")
        #expect(!model.state.busy)
        model.next()
        #expect(model.state.error == nil)
    }

    @Test func addModelPostsTheLeanBody() async throws {
        let stub = DataStub(json: #"{"name":"gem","provider":"gemini","model":"g-2.5"}"#)
        let form = OnboardingAddModelForm()
        #expect(!form.canSubmit)
        await form.testAndSave(client: stub.client) {}
        #expect(form.message == "Name and model are required.")
        #expect(stub.requests.isEmpty)

        form.name = " gem "
        form.model = "g-2.5"
        form.provider = "openai"
        var saves = 0
        await form.testAndSave(client: stub.client) { saves += 1 }
        #expect(stub.last?.path == "/models")
        #expect(try body(stub.last) == ["name": "gem", "provider": "openai", "model": "g-2.5"])
        #expect(form.message == "Model \"gem\" saved.")
        #expect(form.messageOk)
        #expect(saves == 1)
        #expect(form.saved.count == 1)
        #expect(form.name.isEmpty && form.provider == "gemini")

        form.name = "x"
        form.model = "y"
        form.apiKey = " sk-1 "
        await form.testAndSave(client: stub.client) {}
        #expect(try body(stub.last) == ["name": "x", "provider": "gemini", "model": "y", "api_key": "sk-1"])
    }

    @Test func addModelReportsTheApiError() async {
        let stub = DataStub(status: 422, json: #"{"detail":[{"msg":"bad model"}]}"#)
        let form = OnboardingAddModelForm()
        form.name = "a"
        form.model = "b"
        await form.testAndSave(client: stub.client) {}
        #expect(form.message == "bad model")
        #expect(!form.messageOk)
        #expect(!form.busy)
    }

    @Test func linkBrokeragePostsAlpaca() async throws {
        let stub = DataStub(json: #"{"id":"b1","brokerage_type":"alpaca"}"#)
        let form = OnboardingLinkBrokerageForm()
        await form.save(client: stub.client) {}
        #expect(form.message == "Name, API key, and secret are required.")

        form.accountName = " paper "
        form.apiKey = "PK1"
        form.apiSecret = "S1"
        form.paper = false
        await form.save(client: stub.client) {}
        #expect(stub.last?.path == "/brokerages")
        #expect(try body(stub.last) == [
            "account_name": "paper", "brokerage_type": "alpaca", "api_key": "PK1", "api_secret": "S1", "paper": false,
        ])
        // The reply had no account_name, so the body's name is used.
        #expect(form.message == "Brokerage \"paper\" linked.")
        #expect(form.paper)
    }

    @Test func createInstanceValidatesAndMapsTheCadence() async throws {
        let stub = DataStub(json: #"{"instance_id":"my-bot"}"#)
        let form = OnboardingCreateInstanceForm()
        form.instanceId = "My Bot"
        #expect(form.idError == "Only lowercase letters, digits, - and _ allowed.")
        form.instanceId = ""
        #expect(form.idError == nil)
        form.instanceId = "my-bot_1"
        #expect(form.idError == nil)
        #expect(!form.canSubmit)
        form.name = "My First Bot"
        form.cadence = "1hr"
        #expect(form.canSubmit)

        await form.create(client: stub.client) {}
        #expect(stub.last?.path == "/instances")
        #expect(try body(stub.last) == ["instance_id": "my-bot_1", "name": "My First Bot", "cadence": "1h"])
        #expect(form.message == "Instance \"My First Bot\" created.")
        #expect(form.cadence == "5min")
        #expect(OnboardingCreateInstanceForm.cadenceValues == ["1m", "5m", "15m", "1h"])
    }

    @Test func connectLoadsPreselectsAndLinks() async throws {
        let stub = DataStub()
        stub.handler = { request in
            switch request.url?.path {
            case "/instances": (200, #"{"items":[{"id":"inst/1","name":"Bot"}]}"#)
            case "/brokerages": (200, #"{"accounts":[{"id":"b1","account_name":"paper","brokerage_type":"alpaca"}]}"#)
            default: (200, "{}")
            }
        }
        let form = OnboardingConnectForm()
        await form.link(client: stub.client)
        #expect(form.message == "Select both an instance and a brokerage.")

        await form.load(client: stub.client)
        #expect(!form.loading)
        #expect(form.selectedInstance == "inst/1")
        #expect(form.selectedBrokerage == "b1")
        #expect(OnboardingConnectForm.instanceLabel(form.instances[0]) == "Bot")
        #expect(OnboardingConnectForm.brokerageLabel(form.brokerages[0]) == "paper (alpaca)")

        await form.link(client: stub.client)
        #expect(stub.last?.method == "POST")
        #expect(stub.last?.url?.absoluteString.hasSuffix("/instances/inst%2F1/link-brokerage") == true)
        #expect(try body(stub.last) == ["brokerage_id": "b1"])
        #expect(form.message == "Linked! Your instance can now place orders through this brokerage.")
        #expect(form.messageOk)
    }

    @Test func connectLoadFailureShowsTheError() async {
        let stub = DataStub(status: 500, json: #"{"detail":"down"}"#)
        let form = OnboardingConnectForm()
        await form.load(client: stub.client)
        #expect(form.loadError == "down")
        #expect(!form.loading)
    }
}
