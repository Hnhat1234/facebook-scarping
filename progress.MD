### 07/09/2026

Updated about the current project every commit.

### Current

Can scapre posts links.
Can scarpe posts content properly for text only.
Can scarpe a single groups.
Can scrape the post **author** by following the `data-ad-rendering-role="profile_name"` header chain down to the `a -> b -> span` name node.

### Issue

**image_urls** still grabs a lot of junk (avatars, thumbnails, GIFs) — the filter needs to be scoped tighter to actual post attachments.
