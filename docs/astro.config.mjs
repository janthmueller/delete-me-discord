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
      disable404Route: true,
      customCss: ['./src/styles/custom.css'],
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
          label: 'Recipes',
          items: [
            { label: 'Full Channel Cleanup', link: '/guides/full-channel-cleanup/' },
            { label: 'Rolling Retention', link: '/guides/rolling-retention/' },
            { label: 'Recurring Cleanup with Profiles', link: '/guides/recurring-cleanup-with-profiles/' },
          ],
        },
        {
          label: 'Behavior Guides',
          items: [
            { label: 'Delete Reactions', link: '/guides/delete-reactions/' },
            { label: 'Buffered Mode', link: '/guides/buffered-mode/' },
            { label: 'Preserve Cache', link: '/guides/preserve-cache/' },
          ],
        },
        {
          label: 'Reference',
          items: [
            { label: 'CLI Options', link: '/reference/cli-options/' },
            { label: 'Profiles', link: '/reference/profiles/' },
            { label: 'Value Formats', link: '/reference/value-formats/' },
            { label: 'Log Output', link: '/reference/log-output/' },
          ],
        },
      ],
    }),
  ],
});
