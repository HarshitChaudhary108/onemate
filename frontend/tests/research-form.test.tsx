/**
 * Form behaviour (plan M4 tests). The real page is rendered with `fetch`
 * stubbed via `vi.stubGlobal`, so "does not call fetch" and the exact request
 * body are asserted against the shipped code path. No real network.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Home from "@/app/page";

const FIXTURE_DIR = join(
  import.meta.dirname,
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "reports",
);

function loadFixture(name: string): unknown {
  return JSON.parse(readFileSync(join(FIXTURE_DIR, name), "utf-8"));
}

function fakeResponse({ status = 200, body }: { status?: number; body?: unknown }): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

type FetchMock = ReturnType<typeof vi.fn>;

function stubFetch(response: Response | (() => Promise<Response>)): FetchMock {
  const mock = typeof response === "function" ? vi.fn(response) : vi.fn(async () => response);
  vi.stubGlobal("fetch", mock);
  return mock as FetchMock;
}

function requestBody(mock: FetchMock): Record<string, unknown> {
  const [, init] = mock.mock.calls[0] as [string, RequestInit];
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

const REPORT = loadFixture("live_success.json");

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test:8000");
});

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function setup() {
  const user = userEvent.setup();
  render(<Home />);
  const input = screen.getByLabelText("Company name or website");
  const objective = screen.getByLabelText("Research objective (optional)");
  const submit = screen.getByRole("button", { name: "Research company" });
  return { user, input, objective, submit };
}

describe("validation", () => {
  it("shows a field error on empty submit and never calls fetch", async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, submit } = setup();

    await user.click(submit);

    expect(await screen.findByText("Enter a company name or a website.")).toBeInTheDocument();
    expect(mock).not.toHaveBeenCalled();
  });

  it("links field errors with aria-describedby and sets aria-invalid", async () => {
    stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.click(submit);

    await waitFor(() => expect(input).toHaveAttribute("aria-invalid", "true"));
    const describedBy = input.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const ids = (describedBy ?? "").split(/\s+/);
    const errorElement = ids
      .map((id) => document.getElementById(id))
      .find((element) => element?.textContent === "Enter a company name or a website.");
    expect(errorElement).toBeTruthy();
  });

  it("shows backend 422 field errors under the combined input and keeps the value", async () => {
    const validationBody = {
      error: {
        code: "validation_error",
        message: "The request failed validation.",
        request_id: "req-form-422",
        details: [{ field: "domain", msg: "invalid domain: missing host" }],
      },
    };
    stubFetch(fakeResponse({ status: 422, body: validationBody }));
    const { user, input, submit } = setup();

    await user.type(input, "http://");
    await user.click(submit);

    expect(await screen.findByText("invalid domain: missing host")).toBeInTheDocument();
    expect(input).toHaveValue("http://");
    expect(screen.queryByTestId("error-alert")).not.toBeInTheDocument();
  });
});

describe("domain vs company_name classification", () => {
  it('sends "stripe.com" as {domain}', async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.type(input, "stripe.com");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({ domain: "stripe.com" });
  });

  it('sends "Stripe" as {company_name}', async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.type(input, "Stripe");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({ company_name: "Stripe" });
  });

  it('sends "https://www.stripe.com/pricing" as {domain}', async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.type(input, "https://www.stripe.com/pricing");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({ domain: "https://www.stripe.com/pricing" });
  });

  it('sends "Stripe Inc." as {company_name} (whitespace in the input)', async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.type(input, "Stripe Inc.");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({ company_name: "Stripe Inc." });
  });
});

describe("example chip", () => {
  it("fills the input and never submits", async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input } = setup();

    await user.click(screen.getByRole("button", { name: /stripe\.com/ }));

    expect(input).toHaveValue("stripe.com");
    expect(mock).not.toHaveBeenCalled();
  });
});

describe("in-flight behaviour", () => {
  it("disables the submit button and shows progress text while loading", async () => {
    let resolveFetch: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      resolveFetch = resolve;
    });
    stubFetch(() => pending);
    const { user, input, submit } = setup();

    await user.type(input, "stripe.com");
    await user.click(submit);

    const disabled = await screen.findByRole("button", { name: "Researching…" });
    expect(disabled).toBeDisabled();
    expect(screen.getByText("Researching company…")).toBeInTheDocument();
    expect(screen.getByTestId("report-skeleton")).toBeInTheDocument();

    resolveFetch(fakeResponse({ body: REPORT }));
    expect(await screen.findByTestId("report-header")).toBeInTheDocument();
  });
});

describe("objective", () => {
  it("is omitted when blank", async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, submit } = setup();

    await user.type(input, "stripe.com");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({ domain: "stripe.com" });
  });

  it("is sent (trimmed) when non-blank", async () => {
    const mock = stubFetch(fakeResponse({ body: REPORT }));
    const { user, input, objective, submit } = setup();

    await user.type(input, "Stripe");
    await user.type(objective, "  Evaluate fit for a B2B GTM service  ");
    await user.click(submit);

    await waitFor(() => expect(mock).toHaveBeenCalled());
    expect(requestBody(mock)).toEqual({
      company_name: "Stripe",
      research_objective: "Evaluate fit for a B2B GTM service",
    });
  });
});

describe("error states keep form values", () => {
  it("keeps the submitted values after an api_error", async () => {
    stubFetch(fakeResponse({ status: 503, body: loadFixture("api_error.json") }));
    const { user, input, submit } = setup();

    await user.type(input, "example-robotics.test");
    await user.click(submit);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/Exa search is not configured/)).toBeInTheDocument();
    expect(input).toHaveValue("example-robotics.test");
  });
});
