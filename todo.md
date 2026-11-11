1. Running wallpaper playlist service.
It can automatically go over or not local directory of images(sorted, shuffled etc).
History has to be preseved across boots. This is needed for example for shuffle to work properly.
Each monitor can have it's own dir and cycle interval, but dosn't have to, mirroring should be an option.
My main monitor is often turned off for OLED refresh so we need a way to handle this case, maybe postpone the cycle or just skip it entirely, worst case is setting the wallpaper on wrong monitor, this we want to prevent.

1. Way to mark wallpaper favourite, has to be persistent I usually just do .txt files with full paths, but maybe yaml would be better so at least we can split path from filename
1. Way to mark wallpaper as to be removed/deleted
again usally I just do .txt but yaml might be better.
(in my case I don't actually wan't to just delete local image, it might be a symlink to upscaled version of original image,
so I need to pass it first through my lib management script to delete it properly)
1. Way to go to next and previous wallpaper, per monitor(if mirrored then just change all mirrored monitors)

1. Investiage if this is still the preffered way to manage monitors and wallpapers on windows.

## Nice to have

1. Library grid: fade in from black when a thumbnail loads (like the phone app).
1. Sound notification when the wallpaper changes (I have this on Linux already), so I notice new images.