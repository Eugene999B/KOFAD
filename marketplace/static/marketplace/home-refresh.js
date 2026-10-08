document.addEventListener("DOMContentLoaded",()=>{
 const root=document.querySelector("[data-kfd-rotator]");if(!root)return;
 const img=root.querySelector("[data-hero-image]"),c=root.querySelector("[data-hero-count]"),bar=root.querySelector("[data-hero-progress]");
 const local=img.dataset.fallback||"/static/marketplace/kofad-market-retail-hero.webp";
 const photos=[local,
 "https://images.unsplash.com/photo-1542838132-92c53300491e?auto=format&fit=crop&w=1300&q=80",
 "https://images.unsplash.com/photo-1555529669-e69e7aa0ba9a?auto=format&fit=crop&w=1300&q=80",
 "https://images.unsplash.com/photo-1586528116311-ad8dd3c8310d?auto=format&fit=crop&w=1300&q=80",
 "https://images.unsplash.com/photo-1553413077-190dd305871c?auto=format&fit=crop&w=1300&q=80",
 "https://images.unsplash.com/photo-1534723452862-4c874018d66d?auto=format&fit=crop&w=1300&q=80"];
 const bad=new Set();let at=0,timer,loading=false,paused=matchMedia("(prefers-reduced-motion: reduce)").matches;
 const label=()=>{if(c)c.textContent=String(at+1).padStart(2,"0")+" / "+String(photos.length).padStart(2,"0");if(bar)bar.style.width=(100*(at+1)/photos.length)+"%";};
 function show(index,tries=photos.length){if(loading||tries<=0)return;index=(index+photos.length)%photos.length;
 if(bad.has(index))return show(index+1,tries-1);
 if(index===0){img.src=local;at=index;label();return}
 loading=true;const preload=new Image();
 preload.onload=()=>{img.src=preload.src;at=index;label();loading=false};
 preload.onerror=()=>{bad.add(index);loading=false;show(index+1,tries-1)};
 preload.src=photos[index]}
 const stop=()=>{clearInterval(timer);timer=null};
 const run=()=>{stop();if(!paused&&!document.hidden)timer=setInterval(()=>show(at+1),8500)};
 img.addEventListener("error",()=>{img.src=local;at=0;label()});
 root.querySelector("[data-slide-prev]")?.addEventListener("click",()=>{show(at-1);run()});
 root.querySelector("[data-slide-next]")?.addEventListener("click",()=>{show(at+1);run()});
 const pause=root.querySelector("[data-slide-pause]");pause?.addEventListener("click",()=>{paused=!paused;pause.textContent=paused?"▶":"Ⅱ";pause.setAttribute("aria-label",paused?"Resume slideshow":"Pause slideshow");run()});
 root.addEventListener("mouseenter",stop);root.addEventListener("mouseleave",run);
 document.addEventListener("visibilitychange",run);label();run();
});