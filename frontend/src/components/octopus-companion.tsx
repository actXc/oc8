export type OctopusMood = "idle" | "thinking" | "celebrating";

export function OctopusCompanion({ mood }: { mood: OctopusMood }) {
  return (
    <div className="oc8-octopus-companion" data-testid="octopus-companion" data-mood={mood}>
      <img src="/octopus_oc8.svg" alt="" aria-hidden="true" />
    </div>
  );
}
