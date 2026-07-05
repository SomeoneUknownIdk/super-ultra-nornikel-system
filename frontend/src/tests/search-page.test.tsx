import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";
import { SearchPage } from "../features/search/SearchPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter><SearchPage/></MemoryRouter></QueryClientProvider>);
}

describe("SearchPage", () => {
  it("renders an accessible search-first landing", async () => {
    const view = renderPage();
    expect(screen.getByRole("heading", { name: "Знания R&D — в одном ответе" })).toBeInTheDocument();
    const results = await axe(view.container);
    expect(results.violations).toHaveLength(0);
  });

  it("runs the catholyte search and shows numeric facts", async () => {
    const user = userEvent.setup(); renderPage();
    await user.click(screen.getByRole("button", { name: /Циркуляция католита/ }));
    // Answer intent badge + numeric range in rendered markdown
    expect(await screen.findByText("Числовой разбор")).toBeInTheDocument();
    expect(await screen.findByText(/20–30 л\/ч/)).toBeInTheDocument();
    // Facts table exposes the canonical canon "электроэкстракция" (несколько карточек)
    const canonCards = await screen.findAllByText("электроэкстракция");
    expect(canonCards.length).toBeGreaterThan(0);
  });
});
