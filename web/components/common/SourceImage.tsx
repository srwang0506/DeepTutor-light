import { forwardRef, type ImgHTMLAttributes } from 'react'

/** Original documents and generated frames may use cookie-authenticated URLs,
 * browser blobs, or arbitrary user hosts with no known intrinsic dimensions.
 * Keep those bytes in the browser rather than fetching them through an image
 * optimizer. Static application assets should use next/image. */
const SourceImage = forwardRef<HTMLImageElement, ImgHTMLAttributes<HTMLImageElement>>(
  function SourceImage({ alt = '', ...props }, ref) {
    // eslint-disable-next-line @next/next/no-img-element -- Original-source URLs require browser credentials or blob access and retain their natural dimensions.
    return <img ref={ref} alt={alt} {...props} />
  },
)
export default SourceImage
