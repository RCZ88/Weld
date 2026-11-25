Optional offline libraries.

Drop these two files here and the Studio stops using any CDN:

  tailwind.js     from  https://cdn.tailwindcss.com?plugins=forms,container-queries
  three.min.js    three.js r128  (https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js)

Nothing to configure - index.html prefers this folder and falls back to the CDN
when a file is absent.
