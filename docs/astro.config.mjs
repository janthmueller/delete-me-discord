import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

export default defineConfig({
  site: 'https://janthmueller.github.io',
  base: '/delete-me-discord',
  scopedStyleStrategy: 'where',
  integrations: [
    starlight({
      title: 'Delete Me Discord',
      description: 'Documentation for the Delete Me Discord CLI.',
      customCss: ['./src/styles/custom.css'],
      tableOfContents: false,
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/janthmueller/delete-me-discord',
        },
      ],
      sidebar: [
        {
          label: 'Start Here',
          items: [
            { label: 'Overview', link: '/' },
            { label: 'Installation', link: '/getting-started/installation/' },
            { label: 'Authentication', link: '/getting-started/authentication/' },
            { label: 'First Run', link: '/getting-started/first-run/' },
          ],
        },
        {
          label: 'Workflows',
          items: [
            { label: 'Full Channel Cleanup', link: '/guides/full-channel-cleanup/' },
            { label: 'Rolling Retention', link: '/guides/rolling-retention/' },
            { label: 'Delete Reactions', link: '/guides/delete-reactions/' },
            { label: 'Buffered Mode', link: '/guides/buffered-mode/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'CLI Options', link: '/reference/cli-options/' },
            { label: 'Time Deltas', link: '/reference/time-deltas/' },
            { label: 'Preserve Cache', link: '/reference/preserve-cache/' },
            { label: 'Log Output', link: '/reference/log-output/' },
          ],
        },
      ],
    }),
  ],
});
