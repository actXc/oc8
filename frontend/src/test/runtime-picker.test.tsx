import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { RuntimePicker, type RuntimeOption } from "@/components/runtime-picker";

// RuntimePicker's icon (RuntimeIcon) calls useCapaIcon, a react-query hook --
// every render needs a QueryClientProvider now, even the fixtures below that
// never actually resolve an icon (id: null skips the fetch, but the hook is
// still called unconditionally per React's rules).
function renderPicker(props: React.ComponentProps<typeof RuntimePicker>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <RuntimePicker {...props} />
    </QueryClientProvider>,
  );
}

// `name` is deliberately NOT one of the two real built-in names -- these
// fixtures exercise the "one option vs. several" rendering rule, independent
// of the bilingual-copy override `runtimeDisplayCopy` applies to the two
// real defaults (covered by its own test below).
const defaultOnly: RuntimeOption[] = [
  {
    id: null,
    name: "test.default-runtime",
    label: "Default",
    summary: "In-process",
    capabilities: [],
    isDefault: true,
    available: true,
    unavailableReason: null,
  },
];

const twoOptions: RuntimeOption[] = [
  ...defaultOnly,
  {
    id: "abc",
    name: "nanoclaw_runtime",
    label: "nanoclaw",
    summary: "Container runtime",
    capabilities: ["skills"],
    isDefault: false,
    available: true,
    unavailableReason: null,
  },
];

describe("RuntimePicker", () => {
  it("renders a static row with one option", () => {
    renderPicker({ value: null, onChange: vi.fn(), runtimes: defaultOnly });
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByText("Default")).toBeInTheDocument();
  });

  it("renders a radio list with two or more options", () => {
    renderPicker({ value: null, onChange: vi.fn(), runtimes: twoOptions });
    expect(screen.getAllByRole("radio")).toHaveLength(2);
  });

  it("disables an unavailable option and shows its reason", () => {
    const withUnavailable: RuntimeOption[] = [
      ...defaultOnly,
      { ...twoOptions[1], available: false, unavailableReason: "plugin code is not loadable" },
    ];
    renderPicker({ value: null, onChange: vi.fn(), runtimes: withUnavailable });
    const radios = screen.getAllByRole("radio");
    expect(radios[1]).toBeDisabled();
    expect(screen.getByText("plugin code is not loadable")).toBeInTheDocument();
  });

  it("calls onChange with the selected plugin id", () => {
    const onChange = vi.fn();
    renderPicker({ value: null, onChange, runtimes: twoOptions });
    fireEvent.click(screen.getAllByRole("radio")[1]);
    expect(onChange).toHaveBeenCalledWith("abc");
  });

  it("overrides a built-in default's label/summary with the frontend's own bilingual copy", () => {
    // A deliberately WRONG backend label/summary -- if the picker ever
    // rendered these unmodified instead of `runtimeDisplayCopy`'s table,
    // this text would show up and fail the assertion below.
    const builtinDefault: RuntimeOption[] = [
      {
        id: null,
        name: "oc8.agent-runtime",
        label: "should never render",
        summary: "should never render either",
        capabilities: [],
        isDefault: true,
        available: true,
        unavailableReason: null,
      },
    ];
    renderPicker({ value: null, onChange: vi.fn(), runtimes: builtinDefault });
    expect(screen.getByText("Standard (in-process)")).toBeInTheDocument();
    expect(screen.queryByText("should never render")).not.toBeInTheDocument();
    expect(screen.queryByText("should never render either")).not.toBeInTheDocument();
  });

  it("keeps a plugin entry's backend-supplied label/summary unmodified", () => {
    renderPicker({ value: null, onChange: vi.fn(), runtimes: twoOptions });
    expect(screen.getByText("nanoclaw")).toBeInTheDocument();
    expect(screen.getByText("Container runtime")).toBeInTheDocument();
  });
});
