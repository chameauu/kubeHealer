import { StubPage } from '../components/StubPage';

export function Overview() {
  return (
    <StubPage
      title="Overview"
      lead="The cold-start view: what is broken, what is waiting on a human, and how long recovery has been taking."
      sections={[
        {
          title: 'incident pulse',
          body: 'Scenarios pending, running and stopping, with the oldest age first and the fault type attached.',
        },
        {
          title: 'approval queue',
          body: 'Runs parked at the approval interrupt, each with its proposed action, target and risk so the gate can be cleared without opening every run.',
        },
        {
          title: 'recovery record',
          body: 'Median MTTR per fault type and per-action success rates, read from incident memory rather than recomputed in the browser.',
        },
        {
          title: 'source integrity',
          body: 'Which evidence sources answered on the last run — graph, catalogue, chaos backend — with a blind source named, never guessed.',
        },
      ]}
      note="This build ships Incidents and Agent only. Nothing on this page calls a service, so it shows no figures rather than placeholder ones."
    />
  );
}
