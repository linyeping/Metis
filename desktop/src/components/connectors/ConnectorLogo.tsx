import { PlugZap } from 'lucide-react';

import filesystemLogo from '../../assets/connectors/filesystem.svg';
import githubLogo from '../../assets/connectors/github.svg';
import gmailLogo from '../../assets/connectors/gmail.svg';
import googleCalendarLogo from '../../assets/connectors/google-calendar.svg';
import googleDriveLogo from '../../assets/connectors/google-drive.svg';
import notionLogo from '../../assets/connectors/notion.svg';
import postgresqlLogo from '../../assets/connectors/postgresql.svg';
import slackLogo from '../../assets/connectors/slack.svg';
import xLogo from '../../assets/connectors/x.svg';

const CONNECTOR_LOGOS: Record<string, string> = {
  filesystem: filesystemLogo,
  github: githubLogo,
  gmail: gmailLogo,
  google_calendar: googleCalendarLogo,
  google_drive: googleDriveLogo,
  notion: notionLogo,
  postgres: postgresqlLogo,
  slack: slackLogo,
  x_api: xLogo,
  x_docs: xLogo,
};

export function ConnectorLogo({
  serviceId,
  active = false,
  className = '',
}: {
  serviceId: string;
  active?: boolean;
  className?: string;
}) {
  const normalizedServiceId = serviceId.trim().toLowerCase();
  const src = CONNECTOR_LOGOS[normalizedServiceId];

  return (
    <span
      className={`connector-glyph${className ? ` ${className}` : ''}`}
      data-active={active}
      data-brand={normalizedServiceId}
    >
      {src ? <img src={src} alt="" draggable={false} /> : <PlugZap size={15} />}
    </span>
  );
}
