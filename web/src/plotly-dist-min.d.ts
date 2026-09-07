/**
 * `plotly.js-dist-min` ships no types of its own, and `@types/plotly.js` —
 * which IS installed — declares the module name `plotly.js`, not this one.
 *
 * WRITTEN HERE RATHER THAN INSTALLED. `@types/plotly.js-dist-min` exists on
 * npm and does exactly this, but it would be a network fetch for four lines,
 * and this project asks before anything reaches out. The dist build is the
 * same library with the same surface, so re-exporting the existing types is
 * accurate rather than a shim.
 *
 * ONE THING TO KNOW: the installed types are @types/plotly.js v3 and the
 * runtime is plotly.js 4.0.0. The trace, layout and config shapes this tab
 * uses are unchanged between them, but the types are NOT authoritative for
 * v4 additions — anything that typechecks here still has to be seen rendering
 * before it is believed.
 */
declare module 'plotly.js-dist-min' {
  import * as Plotly from 'plotly.js'
  export = Plotly
}
